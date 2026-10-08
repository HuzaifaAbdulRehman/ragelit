import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import pytest
from qdrant_client import QdrantClient

from app.audits.isolation_store import TenantCollectionStore
from app.audits.workspace import AuditWorkspaceError, FixtureBindings
from app.documents.chunking import TextChunk
from app.retrieval.authorization import access_filter
from app.retrieval.store import IndexContext, QdrantChunkStore
from app.tenancy.enums import Role
from app.tenancy.scope import AccessScope
from tests.unit.audits.test_workspace import configuration
from tests.unit.retrieval.test_store_search import DENSE, FixedEmbeddings

pytestmark = pytest.mark.filterwarnings(
    "ignore:Payload indexes have no effect:UserWarning"
)
A, B, USER, GROUP = (UUID(int=value) for value in (1, 2, 3, 4))


@pytest.fixture
def store(tmp_path: Path) -> Iterator[TenantCollectionStore]:
    client = QdrantClient(":memory:")
    target = TenantCollectionStore(
        client,
        configuration(tmp_path, vector_strategy="tenant_collections"),
        dimension=4,
        organizations=lambda: (A, B),
    )
    try:
        target.ensure_tenants((A, B))
        yield target
    finally:
        client.close()


def context(organization: UUID, number: int = 0) -> IndexContext:
    return IndexContext(
        organization,
        UUID(int=number + 20),
        UUID(int=number + 30),
        UUID(int=number + 50),
        "synthetic.txt",
        "organization",
        (),
        (),
    )


def stage(store: QdrantChunkStore, value: IndexContext, chunk: int) -> None:
    store.stage(
        value,
        (TextChunk(UUID(int=chunk), 0, "synthetic evidence", "line 1"),),
        FixedEmbeddings(DENSE),
    )
    store.activate(value)


def search(
    store: QdrantChunkStore,
    value: IndexContext,
    groups: tuple[UUID, ...] = (),
) -> list[str]:
    scope = AccessScope(USER, value.organization_id, UUID(int=5), Role.MEMBER, groups)
    versions = (value.version_id,)
    result = store.search_points(
        value.organization_id,
        versions,
        DENSE,
        access_filter(scope, versions),
        10,
    )
    assert result.raw == result.accepted
    return [
        str(point.payload["chunk_id"]) for point in result.accepted if point.payload
    ]


def test_real_writes_and_queries_stay_in_each_tenant_collection(
    store: TenantCollectionStore,
) -> None:
    first, second = context(A), context(B, 1)
    stage(store, first, 40)
    stage(store, second, 41)
    assert store.collection_names() == (
        "ragelit_audit_unit_tenant_00000000000000000000000000000001",
        "ragelit_audit_unit_tenant_00000000000000000000000000000002",
    )
    assert not store.client.collection_exists("ragelit_audit_unit")
    assert [
        store.client.count(name, exact=True).count for name in store.collection_names()
    ] == [1, 1]
    assert search(store, first) == [str(UUID(int=40))]
    assert search(store, second) == [str(UUID(int=41))]
    assert search(store, first) == [str(UUID(int=40))]
    for organization, name in zip((A, B), store.collection_names(), strict=True):
        points, offset = store.client.scroll(name)
        assert offset is None and len(points) == 1
        assert points[0].payload is not None
        assert points[0].payload["organization_id"] == str(organization)


def test_physical_separation_does_not_replace_direct_and_group_grants(
    store: TenantCollectionStore,
) -> None:
    document = context(A)
    stage(store, document, 40)
    store.set_access(A, document.document_id, "restricted", (USER,), ())
    assert search(store, document) == [str(UUID(int=40))]
    store.set_access(A, document.document_id, "restricted", (), (GROUP,))
    assert search(store, document) == []
    assert search(store, document, (GROUP,)) == [str(UUID(int=40))]
    store.set_access(A, document.document_id, "restricted", (), ())
    assert search(store, document, (GROUP,)) == []


def test_replacement_and_deactivation_do_not_change_another_tenant(
    store: TenantCollectionStore,
) -> None:
    old, foreign = context(A), context(B, 1)
    stage(store, old, 40)
    stage(store, foreign, 41)
    replacement = IndexContext(
        A,
        old.document_id,
        UUID(int=32),
        UUID(int=52),
        "synthetic.txt",
        "organization",
        (),
        (),
    )
    stage(store, replacement, 42)
    assert search(store, old) == []
    assert search(store, replacement) == [str(UUID(int=42))]
    assert search(store, foreign) == [str(UUID(int=41))]
    store.deactivate(A, old.document_id)
    assert search(store, replacement) == []
    assert search(store, foreign) == [str(UUID(int=41))]
    assert [
        store.client.count(name, exact=True).count for name in store.collection_names()
    ] == [2, 1]


@pytest.mark.parametrize(
    "operation", ["stage", "activate", "access", "deactivate", "search"]
)
def test_unknown_organization_is_rejected_before_touching_storage(
    store: TenantCollectionStore,
    operation: str,
) -> None:
    unknown = context(UUID(int=999))
    before = tuple(item.name for item in store.client.get_collections().collections)
    with pytest.raises(AuditWorkspaceError, match="audit_unknown_organization"):
        if operation == "stage":
            stage(store, unknown, 40)
        elif operation == "activate":
            store.activate(unknown)
        elif operation == "access":
            store.set_access(
                unknown.organization_id, unknown.document_id, "organization", (), ()
            )
        elif operation == "deactivate":
            store.deactivate(unknown.organization_id, unknown.document_id)
        else:
            search(store, unknown)
    assert before == tuple(
        item.name for item in store.client.get_collections().collections
    )
    assert [
        store.client.count(name, exact=True).count for name in store.collection_names()
    ] == [0, 0]


def test_existing_collection_is_neither_adopted_nor_reset(tmp_path: Path) -> None:
    client = QdrantClient(":memory:")
    existing = "ragelit_audit_unit_tenant_00000000000000000000000000000002"
    ordinary = QdrantChunkStore(client, existing, dimension=4)
    try:
        ordinary.ensure_collection()
        stage(ordinary, context(B), 41)
        target = TenantCollectionStore(
            client,
            configuration(tmp_path, vector_strategy="tenant_collections"),
            dimension=4,
            organizations=lambda: (A, B),
        )
        with pytest.raises(AuditWorkspaceError, match="unowned_audit_collection"):
            target.ensure_tenants((A, B))
        assert client.count(existing, exact=True).count == 1
        assert not client.collection_exists(
            "ragelit_audit_unit_tenant_00000000000000000000000000000001"
        )
    finally:
        client.close()


def test_default_configuration_hash_and_binding_schema_remain_unchanged(
    tmp_path: Path,
) -> None:
    default = configuration(tmp_path)
    legacy = {
        "environment": "test",
        "database": "ragelit_audit_unit",
        "database_host": "127.0.0.1",
        "database_port": 5432,
        "qdrant_host": "127.0.0.1",
        "qdrant_port": 6333,
        "qdrant_timeout_seconds": 30,
        "collection": "ragelit_audit_unit",
        "root": str(default.root.resolve()),
    }
    expected = hashlib.sha256(
        json.dumps(legacy, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert default.config_hash == expected
    assert (
        configuration(tmp_path, vector_strategy="shared_pre_filter").config_hash
        == expected
    )
    assert (
        configuration(tmp_path, vector_strategy="tenant_collections").config_hash
        != expected
    )
    bindings = FixtureBindings(
        workspace_id=default.workspace_id,
        template_hash="a" * 64,
        collection=default.name,
    )
    assert set(bindings.model_dump()) == {
        "schema_version",
        "workspace_id",
        "template_hash",
        "collection",
        "seeded",
        "organizations",
        "actors",
        "memberships",
        "groups",
        "documents",
        "instances",
        "rows",
        "points",
    }


@pytest.mark.parametrize("unsafe", ["production", "shared", "duplicate_registry"])
def test_strategy_configuration_cannot_bypass_owned_registry(
    tmp_path: Path,
    unsafe: str,
) -> None:
    config = configuration(tmp_path, vector_strategy="tenant_collections")
    if unsafe == "production":
        config = config.model_copy(update={"environment": "production"})
    elif unsafe == "shared":
        config = configuration(tmp_path)
    client = QdrantClient(":memory:")
    try:
        with pytest.raises(AuditWorkspaceError):
            target = TenantCollectionStore(
                client,
                config,
                dimension=4,
                organizations=lambda: (
                    (A, A) if unsafe == "duplicate_registry" else (A, B)
                ),
            )
            target.ensure_tenants((A, B))
        assert client.get_collections().collections == []
    finally:
        client.close()
