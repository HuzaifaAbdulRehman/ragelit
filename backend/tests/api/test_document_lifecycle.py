from collections.abc import Callable
from pathlib import Path
from typing import cast
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.retrieval.embeddings import Embedding
from app.retrieval.service import AuthorizedRetriever
from app.retrieval.store import QdrantChunkStore
from app.tenancy.rls import set_request_context
from app.workers.ingestion import run_once
from tests.api.document_support import TestEmbeddings
from tests.api.tenant_support import TenantApiSeed
from tests.api.test_retrieval import indexed_document, scope_for


def test_grant_editor_retrieval_and_revocation(
    tenant_client: TestClient,
    tenant_seed: TenantApiSeed,
    tenant_database_engines: tuple[Engine, Engine],
    login_headers: Callable[[str, str], dict[str, str]],
    vector_store: QdrantChunkStore,
) -> None:
    admin, runtime = tenant_database_engines
    scope = scope_for(admin, tenant_seed.member_email, tenant_seed.organization_a_id)
    document_id, _ = indexed_document(admin, vector_store, scope.organization_id)
    cast(FastAPI, tenant_client.app).state.chunk_store = vector_store
    owner = login_headers(tenant_seed.owner_email, tenant_seed.organization_a_slug)
    grant = tenant_client.patch(
        f"/api/v1/documents/{document_id}",
        headers=owner,
        json={
            "visibility": "restricted",
            "user_ids": [str(scope.user_id)],
            "group_ids": [],
        },
    )
    assert grant.status_code == 200
    with Session(runtime) as session:
        set_request_context(
            session, user_id=scope.user_id, organization_id=scope.organization_id
        )
        assert AuthorizedRetriever(session, vector_store, TestEmbeddings()).search(
            scope, "evidence", 10
        )
    revoke = tenant_client.patch(
        f"/api/v1/documents/{document_id}",
        headers=owner,
        json={"visibility": "restricted", "user_ids": [], "group_ids": []},
    )
    assert revoke.status_code == 200
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


def test_foreign_group_grant_is_rejected_without_projection_change(
    tenant_client: TestClient,
    tenant_seed: TenantApiSeed,
    tenant_database_engines: tuple[Engine, Engine],
    login_headers: Callable[[str, str], dict[str, str]],
    vector_store: QdrantChunkStore,
) -> None:
    admin, _ = tenant_database_engines
    document_id, _ = indexed_document(
        admin, vector_store, tenant_seed.organization_a_id
    )
    cast(FastAPI, tenant_client.app).state.chunk_store = vector_store
    owner = login_headers(tenant_seed.owner_email, tenant_seed.organization_a_slug)
    response = tenant_client.patch(
        f"/api/v1/documents/{document_id}",
        headers=owner,
        json={
            "visibility": "restricted",
            "group_ids": [str(tenant_seed.group_b_id)],
            "user_ids": [],
        },
    )
    assert response.status_code == 404
    assert tenant_seed.group_b_name not in response.text
    points, _ = vector_store.client.scroll(vector_store.collection_name)
    assert (
        points[0].payload is not None and points[0].payload["allowed_group_ids"] == []
    )


def test_projection_failure_keeps_old_grants_and_returns_retriable_error(
    tenant_client: TestClient,
    tenant_seed: TenantApiSeed,
    tenant_database_engines: tuple[Engine, Engine],
    login_headers: Callable[[str, str], dict[str, str]],
    vector_store: QdrantChunkStore,
) -> None:
    admin, runtime = tenant_database_engines
    scope = scope_for(admin, tenant_seed.member_email, tenant_seed.organization_a_id)
    document_id, _ = indexed_document(
        admin, vector_store, scope.organization_id, user_id=scope.user_id
    )
    client = QdrantClient(
        url="http://127.0.0.1:1", timeout=1, check_compatibility=False
    )
    cast(FastAPI, tenant_client.app).state.chunk_store = QdrantChunkStore(
        client, vector_store.collection_name, dimension=4
    )
    owner = login_headers(tenant_seed.owner_email, tenant_seed.organization_a_slug)
    try:
        response = tenant_client.patch(
            f"/api/v1/documents/{document_id}",
            headers=owner,
            json={"visibility": "restricted", "user_ids": [], "group_ids": []},
        )
        assert response.status_code == 503
        assert response.json()["code"] == "projection_unavailable"
        with Session(runtime) as session:
            set_request_context(
                session, user_id=scope.user_id, organization_id=scope.organization_id
            )
            assert AuthorizedRetriever(session, vector_store, TestEmbeddings()).search(
                scope, "evidence", 10
            )
    finally:
        client.close()


def test_deletion_removes_searchability_before_reporting_success(
    tenant_client: TestClient,
    tenant_seed: TenantApiSeed,
    tenant_database_engines: tuple[Engine, Engine],
    login_headers: Callable[[str, str], dict[str, str]],
    vector_store: QdrantChunkStore,
) -> None:
    admin, runtime = tenant_database_engines
    scope = scope_for(admin, tenant_seed.member_email, tenant_seed.organization_a_id)
    document_id, _ = indexed_document(
        admin, vector_store, scope.organization_id, visibility="organization"
    )
    cast(FastAPI, tenant_client.app).state.chunk_store = vector_store
    owner = login_headers(tenant_seed.owner_email, tenant_seed.organization_a_slug)
    response = tenant_client.delete(f"/api/v1/documents/{document_id}", headers=owner)
    assert response.status_code == 204
    assert (
        tenant_client.get(f"/api/v1/documents/{document_id}", headers=owner).status_code
        == 404
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
    points, _ = vector_store.client.scroll(vector_store.collection_name)
    assert all(point.payload and point.payload["active"] is False for point in points)


def test_replacement_supersedes_old_version_and_preserves_grants(
    tenant_client: TestClient,
    tenant_seed: TenantApiSeed,
    tmp_path: Path,
    tenant_database_engines: tuple[Engine, Engine],
    login_headers: Callable[[str, str], dict[str, str]],
    vector_store: QdrantChunkStore,
) -> None:
    admin, runtime = tenant_database_engines
    scope = scope_for(admin, tenant_seed.member_email, tenant_seed.organization_a_id)
    document_id, old_chunk = indexed_document(
        admin, vector_store, scope.organization_id, user_id=scope.user_id
    )
    state = cast(FastAPI, tenant_client.app).state
    state.chunk_store = vector_store
    state.settings = state.settings.model_copy(update={"data_dir": tmp_path})
    owner = login_headers(tenant_seed.owner_email, tenant_seed.organization_a_slug)
    response = tenant_client.post(
        f"/api/v1/documents/{document_id}/versions?filename=updated.txt",
        headers={**owner, "Content-Type": "text/plain"},
        content=b"Replacement evidence",
    )
    assert response.status_code == 202
    assert UUID(response.json()["id"]) == document_id
    assert run_once(
        scope.organization_id,
        factory=sessionmaker(bind=runtime, expire_on_commit=False),
        settings=state.settings,
        store=vector_store,
        embeddings=TestEmbeddings(),
    )
    versions = tenant_client.get(
        f"/api/v1/documents/{document_id}/versions", headers=owner
    )
    assert versions.status_code == 200
    assert {version["state"] for version in versions.json()} == {"ready", "superseded"}
    with Session(runtime) as session:
        set_request_context(
            session, user_id=scope.user_id, organization_id=scope.organization_id
        )
        chunks = AuthorizedRetriever(session, vector_store, TestEmbeddings()).search(
            scope, "evidence", 10
        )
        assert chunks and old_chunk not in [chunk.id for chunk in chunks]
        assert chunks[0].text == "Replacement evidence"


def test_retry_recovers_the_same_uploaded_version(
    tenant_client: TestClient,
    tenant_seed: TenantApiSeed,
    tmp_path: Path,
    tenant_database_engines: tuple[Engine, Engine],
    login_headers: Callable[[str, str], dict[str, str]],
    vector_store: QdrantChunkStore,
) -> None:
    state = cast(FastAPI, tenant_client.app).state
    state.settings = state.settings.model_copy(update={"data_dir": tmp_path})
    owner = login_headers(tenant_seed.owner_email, tenant_seed.organization_a_slug)
    response = tenant_client.post(
        "/api/v1/documents?filename=retry.txt",
        headers={**owner, "Content-Type": "text/plain"},
        content=b"Retryable evidence",
    )
    assert response.status_code == 202
    document_id = response.json()["id"]
    _, runtime = tenant_database_engines
    factory = sessionmaker(bind=runtime, expire_on_commit=False)

    class FailedEmbeddings(TestEmbeddings):
        def documents(self, texts: list[str]) -> list[Embedding]:
            raise OSError("temporary model failure")

    assert run_once(
        tenant_seed.organization_a_id,
        factory=factory,
        settings=state.settings,
        store=vector_store,
        embeddings=FailedEmbeddings(),
    )
    original = tenant_client.get(
        f"/api/v1/documents/{document_id}/versions", headers=owner
    ).json()
    assert len(original) == 1 and original[0]["state"] == "failed"
    retry = tenant_client.post(f"/api/v1/documents/{document_id}/retry", headers=owner)
    assert retry.status_code == 202
    assert run_once(
        tenant_seed.organization_a_id,
        factory=factory,
        settings=state.settings,
        store=vector_store,
        embeddings=TestEmbeddings(),
    )
    final = tenant_client.get(
        f"/api/v1/documents/{document_id}/versions", headers=owner
    ).json()
    assert len(final) == 1 and final[0]["id"] == original[0]["id"]
    assert final[0]["state"] == "ready"
