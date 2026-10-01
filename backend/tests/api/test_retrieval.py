from uuid import UUID, uuid4

import pytest
from qdrant_client import QdrantClient
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

from app.documents.chunking import TextChunk
from app.documents.extraction import DocumentError
from app.documents.models import Document, DocumentGrant, DocumentVersion
from app.identity.models import User
from app.retrieval.service import AuthorizedRetriever
from app.retrieval.store import IndexContext, QdrantChunkStore
from app.tenancy.models import GroupMember, Membership
from app.tenancy.rls import set_request_context
from app.tenancy.scope import AccessScope
from tests.api.document_support import TestEmbeddings
from tests.api.tenant_support import TenantApiSeed


def scope_for(
    admin: Engine, email: str, organization_id: UUID, group_ids: tuple[UUID, ...] = ()
) -> AccessScope:
    with Session(admin) as session:
        row = session.execute(
            select(User, Membership)
            .join(Membership, Membership.user_id == User.id)
            .where(User.email == email, Membership.organization_id == organization_id)
        ).one()
        user, membership = row
        return AccessScope(
            user.id, organization_id, membership.id, membership.role, group_ids
        )


def indexed_document(
    admin: Engine,
    store: QdrantChunkStore,
    organization_id: UUID,
    *,
    visibility: str = "restricted",
    user_id: UUID | None = None,
    group_id: UUID | None = None,
) -> tuple[UUID, UUID]:
    with Session(admin, expire_on_commit=False) as session:
        document = Document(
            organization_id=organization_id,
            filename="private.txt",
            media_type="text/plain",
            checksum=uuid4().hex * 2,
            state="ready",
            visibility=visibility,
        )
        session.add(document)
        session.flush()
        version = DocumentVersion(
            organization_id=organization_id,
            document_id=document.id,
            storage_key="synthetic",
            checksum=document.checksum,
            byte_count=1,
            state="ready",
            chunk_count=1,
        )
        session.add(version)
        session.flush()
        document.current_version_id = version.id
        if user_id is not None or group_id is not None:
            session.add(
                DocumentGrant(
                    organization_id=organization_id,
                    document_id=document.id,
                    user_id=user_id,
                    group_id=group_id,
                )
            )
        session.commit()
    context = IndexContext(
        organization_id,
        document.id,
        version.id,
        uuid4(),
        document.filename,
        visibility,
        (user_id,) if user_id else (),
        (group_id,) if group_id else (),
    )
    chunk_id = uuid4()
    store.stage(
        context,
        (TextChunk(chunk_id, 0, "Synthetic source evidence", "line 1"),),
        TestEmbeddings(),
    )
    store.activate(context)
    return document.id, chunk_id


@pytest.mark.parametrize("visibility", ["organization", "direct", "group"])
def test_real_hybrid_search_returns_permitted_evidence(
    tenant_database_engines: tuple[Engine, Engine],
    tenant_seed: TenantApiSeed,
    vector_store: QdrantChunkStore,
    visibility: str,
) -> None:
    admin, runtime = tenant_database_engines
    scope = scope_for(admin, tenant_seed.member_email, tenant_seed.organization_a_id)
    if visibility == "group":
        with Session(admin) as session:
            session.add(
                GroupMember(
                    organization_id=scope.organization_id,
                    group_id=tenant_seed.group_a_id,
                    user_id=scope.user_id,
                )
            )
            session.commit()
    document_id, chunk_id = indexed_document(
        admin,
        vector_store,
        scope.organization_id,
        visibility="organization" if visibility == "organization" else "restricted",
        user_id=scope.user_id if visibility == "direct" else None,
        group_id=tenant_seed.group_a_id if visibility == "group" else None,
    )
    with Session(runtime) as session:
        set_request_context(
            session, user_id=scope.user_id, organization_id=scope.organization_id
        )
        result = AuthorizedRetriever(session, vector_store, TestEmbeddings()).search(
            scope, "evidence", 10
        )
        assert [(chunk.document_id, chunk.id) for chunk in result] == [
            (document_id, chunk_id)
        ]
        assert result[0].text == "Synthetic source evidence"


def test_foreign_tenant_and_forged_group_scope_never_return_chunks(
    tenant_database_engines: tuple[Engine, Engine],
    tenant_seed: TenantApiSeed,
    vector_store: QdrantChunkStore,
) -> None:
    admin, runtime = tenant_database_engines
    scope = scope_for(
        admin,
        tenant_seed.member_email,
        tenant_seed.organization_a_id,
        (tenant_seed.group_a_id,),
    )
    indexed_document(
        admin, vector_store, tenant_seed.organization_b_id, visibility="organization"
    )
    indexed_document(
        admin, vector_store, scope.organization_id, group_id=tenant_seed.group_a_id
    )
    with Session(runtime) as session:
        set_request_context(
            session, user_id=scope.user_id, organization_id=scope.organization_id
        )
        assert (
            AuthorizedRetriever(session, vector_store, TestEmbeddings()).search(
                scope, "evidence", 10
            )
            == ()
        )


def test_revoked_grant_is_excluded_with_stale_qdrant_payload(
    tenant_database_engines: tuple[Engine, Engine],
    tenant_seed: TenantApiSeed,
    vector_store: QdrantChunkStore,
) -> None:
    admin, runtime = tenant_database_engines
    scope = scope_for(admin, tenant_seed.member_email, tenant_seed.organization_a_id)
    document_id, _ = indexed_document(
        admin, vector_store, scope.organization_id, user_id=scope.user_id
    )
    with Session(admin) as session:
        session.execute(
            delete(DocumentGrant).where(DocumentGrant.document_id == document_id)
        )
        session.commit()
    with Session(runtime) as session:
        set_request_context(
            session, user_id=scope.user_id, organization_id=scope.organization_id
        )
        assert (
            AuthorizedRetriever(session, vector_store, TestEmbeddings()).search(
                scope, "evidence", 10
            )
            == ()
        )


def test_inactive_membership_cannot_reuse_previous_access_scope(
    tenant_database_engines: tuple[Engine, Engine],
    tenant_seed: TenantApiSeed,
    vector_store: QdrantChunkStore,
) -> None:
    admin, runtime = tenant_database_engines
    scope = scope_for(admin, tenant_seed.member_email, tenant_seed.organization_a_id)
    indexed_document(
        admin, vector_store, scope.organization_id, visibility="organization"
    )
    with Session(admin) as session:
        membership = session.get(Membership, scope.membership_id)
        assert membership is not None
        membership.is_active = False
        session.commit()
    with Session(runtime) as session:
        set_request_context(
            session, user_id=scope.user_id, organization_id=scope.organization_id
        )
        with pytest.raises(DocumentError, match="membership_inactive"):
            AuthorizedRetriever(session, vector_store, TestEmbeddings()).search(
                scope, "evidence", 10
            )


def test_owner_role_does_not_grant_restricted_document_read(
    tenant_database_engines: tuple[Engine, Engine],
    tenant_seed: TenantApiSeed,
    vector_store: QdrantChunkStore,
) -> None:
    admin, runtime = tenant_database_engines
    scope = scope_for(admin, tenant_seed.owner_email, tenant_seed.organization_a_id)
    indexed_document(admin, vector_store, scope.organization_id)
    with Session(runtime) as session:
        set_request_context(
            session, user_id=scope.user_id, organization_id=scope.organization_id
        )
        assert (
            AuthorizedRetriever(session, vector_store, TestEmbeddings()).search(
                scope, "evidence", 10
            )
            == ()
        )


@pytest.mark.parametrize("state", ["deleted", "failed", "processing"])
def test_stale_active_points_cannot_bypass_document_lifecycle(
    tenant_database_engines: tuple[Engine, Engine],
    tenant_seed: TenantApiSeed,
    vector_store: QdrantChunkStore,
    state: str,
) -> None:
    admin, runtime = tenant_database_engines
    scope = scope_for(admin, tenant_seed.member_email, tenant_seed.organization_a_id)
    document_id, _ = indexed_document(
        admin, vector_store, scope.organization_id, visibility="organization"
    )
    with Session(admin) as session:
        document = session.get(Document, document_id)
        assert document is not None
        document.state = state
        session.commit()
    with Session(runtime) as session:
        set_request_context(
            session, user_id=scope.user_id, organization_id=scope.organization_id
        )
        assert (
            AuthorizedRetriever(session, vector_store, TestEmbeddings()).search(
                scope, "evidence", 10
            )
            == ()
        )


def test_unavailable_qdrant_has_no_unfiltered_fallback(
    tenant_database_engines: tuple[Engine, Engine],
    tenant_seed: TenantApiSeed,
    vector_store: QdrantChunkStore,
) -> None:
    admin, runtime = tenant_database_engines
    scope = scope_for(admin, tenant_seed.member_email, tenant_seed.organization_a_id)
    indexed_document(
        admin, vector_store, scope.organization_id, visibility="organization"
    )
    client = QdrantClient(
        url="http://127.0.0.1:1", timeout=1, check_compatibility=False
    )
    unavailable = QdrantChunkStore(client, vector_store.collection_name, dimension=4)
    try:
        with Session(runtime) as session:
            set_request_context(
                session, user_id=scope.user_id, organization_id=scope.organization_id
            )
            with pytest.raises(DocumentError, match="retrieval_unavailable"):
                AuthorizedRetriever(session, unavailable, TestEmbeddings()).search(
                    scope, "evidence", 10
                )
    finally:
        client.close()
