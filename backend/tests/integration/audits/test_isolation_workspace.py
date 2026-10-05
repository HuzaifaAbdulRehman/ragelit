from collections.abc import Iterator
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
from qdrant_client import QdrantClient, models
from sqlalchemy.orm import Session

from app.audits.fixtures import generate_fixtures
from app.audits.seeding import seed_workspace
from app.audits.workspace import AuditConfiguration, AuditWorkspace, AuditWorkspaceError
from app.tenancy.models import Organization
from tests.integration.audits.support import audit_config as audit_config


@dataclass
class TenantResources:
    config: AuditConfiguration
    collections: set[str] = field(default_factory=set)

    def capture(self, workspace: AuditWorkspace) -> None:
        self.collections.update(workspace.store.collection_names())


@pytest.fixture
def owned(audit_config: AuditConfiguration) -> Iterator[TenantResources]:
    config = AuditConfiguration.model_validate(
        audit_config.model_dump() | {"vector_strategy": "tenant_collections"}
    )
    resources = TenantResources(config)
    try:
        yield resources
    finally:
        config.validate_paths()
        client = QdrantClient(url=config.qdrant_url, trust_env=False)
        try:
            for name in sorted(resources.collections):
                prefix = f"{config.name}_tenant_"
                assert name.startswith(prefix)
                identifier = UUID(hex=name.removeprefix(prefix))
                assert name == f"{prefix}{identifier.hex}"
                if client.collection_exists(name):
                    client.delete_collection(name)
        finally:
            client.close()


def register_owned_organizations(
    workspace: AuditWorkspace, owned: TenantResources
) -> None:
    with workspace.mutation():
        organizations = {org.id: uuid4() for org in workspace.template.organizations}
        with Session(workspace.admin_engine) as session:
            session.add_all(
                [
                    Organization(id=organizations[org.id], name=org.id, slug=org.slug)
                    for org in workspace.template.organizations
                ]
            )
            session.commit()
        workspace.bindings = workspace.bindings.model_copy(
            update={"organizations": organizations}
        )
        owned.capture(workspace)
        workspace.store.ensure_tenants(tuple(organizations.values()))


def test_tenant_workspace_seeds_real_ingestion_and_reopens_without_reseeding(
    owned: TenantResources,
) -> None:
    template = generate_fixtures()
    with AuditWorkspace(owned.config, template) as workspace:
        try:
            bindings = seed_workspace(workspace, template)
        finally:
            owned.capture(workspace)
        assert len(bindings.documents) == 27
        assert len(workspace.store.collection_names()) == 3
        assert not workspace.store.client.collection_exists(owned.config.name)
        assert [
            workspace.store.client.count(name, exact=True).count
            for name in workspace.store.collection_names()
        ] == [9, 9, 9]
        assert len(bindings.points) == 27
        assert all(
            key.startswith(f"{owned.config.name}_tenant_") for key in bindings.points
        )
        before = bindings.checksum
        assert seed_workspace(workspace, template).checksum == before
        for name in workspace.store.collection_names():
            info = workspace.store.client.get_collection(name)
            assert info.payload_schema["organization_id"].params is not None
            points, offset = workspace.store.client.scroll(name, limit=100)
            assert offset is None and len(points) == 9
            assert all(
                point.payload is not None
                and point.payload["organization_id"]
                == str(UUID(hex=name.rsplit("_", 1)[1]))
                for point in points
            )
    with AuditWorkspace(owned.config, template) as workspace:
        assert seed_workspace(workspace, template).checksum == before
        owned.capture(workspace)
    shared = AuditConfiguration.model_validate(
        owned.config.model_dump() | {"vector_strategy": "shared_pre_filter"}
    )
    with pytest.raises(AuditWorkspaceError, match="audit_marker_drift"):
        with AuditWorkspace(shared, template):
            pytest.fail("strategy change adopted a tenant workspace")


@pytest.mark.parametrize("drift", ["missing", "extra", "point"])
def test_owned_snapshot_rejects_collection_and_point_drift(
    owned: TenantResources,
    drift: str,
) -> None:
    with AuditWorkspace(owned.config, generate_fixtures()) as workspace:
        register_owned_organizations(workspace, owned)
        names = workspace.store.collection_names()
        if drift == "missing":
            workspace.store.client.delete_collection(names[0])
        elif drift == "extra":
            name = f"{owned.config.name}_tenant_{UUID(int=999).hex}"
            owned.collections.add(name)
            workspace.store.client.create_collection(
                name,
                vectors_config=models.VectorParams(
                    size=64, distance=models.Distance.COSINE
                ),
            )
        else:
            workspace.store.client.upsert(
                names[0],
                points=[
                    models.PointStruct(
                        id=str(uuid4()),
                        vector={
                            "dense": [1.0] + [0.0] * 63,
                            "sparse": models.SparseVector(indices=[0], values=[1.0]),
                        },
                        payload={"text": "UnknownSyntheticMarker"},
                    )
                ],
                wait=True,
            )
        with pytest.raises(AuditWorkspaceError, match="audit_resource_drift"):
            with workspace.mutation():
                pytest.fail("drifted workspace allowed another mutation")
        if drift == "point":
            assert workspace.store.client.count(names[0], exact=True).count == 1
        elif drift == "extra":
            assert workspace.store.client.collection_exists(name)


def test_interrupted_tenant_seeding_is_not_reset_or_silently_reused(
    owned: TenantResources,
) -> None:
    template = generate_fixtures()
    with AuditWorkspace(owned.config, template) as workspace:
        register_owned_organizations(workspace, owned)
        before = workspace.bindings.checksum
    with AuditWorkspace(owned.config, template) as workspace:
        with pytest.raises(AuditWorkspaceError, match="audit_seed_incomplete"):
            seed_workspace(workspace, template)
        assert workspace.bindings.checksum == before
        assert not workspace.bindings.seeded
        assert len(workspace.bindings.organizations) == 3
        assert [
            workspace.store.client.count(name, exact=True).count
            for name in workspace.store.collection_names()
        ] == [0, 0, 0]


def test_unowned_existing_namespace_is_refused_without_reset(
    owned: TenantResources,
) -> None:
    name = f"{owned.config.name}_tenant_{UUID(int=999).hex}"
    owned.collections.add(name)
    client = QdrantClient(url=owned.config.qdrant_url, trust_env=False)
    try:
        client.create_collection(
            name,
            vectors_config=models.VectorParams(
                size=64, distance=models.Distance.COSINE
            ),
        )
        client.upsert(
            name,
            points=[
                models.PointStruct(
                    id=str(uuid4()),
                    vector=[1.0] + [0.0] * 63,
                    payload={"text": "keep owned test evidence"},
                ),
            ],
            wait=True,
        )
        with pytest.raises(AuditWorkspaceError, match="unowned_audit_workspace"):
            with AuditWorkspace(owned.config, generate_fixtures()):
                pytest.fail("pre-existing namespace was adopted")
        assert client.count(name, exact=True).count == 1
        assert not owned.config.root.exists()
    finally:
        client.close()
