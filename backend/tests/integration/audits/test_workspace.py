import json
from uuid import uuid4

import pytest
from qdrant_client import models
from sqlalchemy import create_engine, insert, select, text
from sqlalchemy.engine import make_url

from app.audits.fixtures import generate_fixtures
from app.audits.seeding import seed_workspace
from app.audits.workspace import AuditConfiguration, AuditWorkspace, AuditWorkspaceError
from app.documents.models import DocumentVersion
from app.identity.models import User
from tests.integration.audits.support import audit_config as audit_config


def test_workspace_does_not_inherit_the_ordinary_app_environment(
    audit_config: AuditConfiguration,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAGELIT_ACCESS_TOKEN_MINUTES", "1")
    monkeypatch.setenv("RAGELIT_MAX_UPLOAD_BYTES", "1")
    monkeypatch.setenv("RAGELIT_QDRANT_COLLECTION", "ordinary_collection")
    monkeypatch.setenv("RAGELIT_LLM_BASE_URL", "https://provider.example.invalid")
    with AuditWorkspace(audit_config, generate_fixtures()) as workspace:
        assert workspace.settings.access_token_minutes == 15
        assert workspace.settings.max_upload_bytes == 25 * 1024 * 1024
        assert workspace.settings.qdrant_collection == audit_config.name
        assert workspace.settings.llm_base_url is None


def test_seed_uses_real_ingestion_non_owner_rls_and_reuses_base_bindings(
    audit_config: AuditConfiguration,
) -> None:
    template = generate_fixtures()
    with AuditWorkspace(audit_config, template) as workspace:
        bindings = seed_workspace(workspace, template)
        assert len(bindings.documents) == 27
        assert len(bindings.actors) == 21
        with workspace.factory() as session:
            assert (
                session.execute(text("SELECT current_user")).scalar_one()
                == audit_config.application_role
            )
            assert (
                session.execute(text("SELECT count(*) FROM memberships")).scalar_one()
                == 0
            )
            flags = session.execute(
                text(
                    "SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            ).one()
            assert flags == (False, False, False, False)
        with workspace.admin_engine.connect() as connection:
            flags = connection.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE relname = 'documents'"
                )
            ).one()
            assert flags == (True, True)
            versions = connection.execute(
                select(DocumentVersion.state, DocumentVersion.chunk_count)
            ).all()
            assert len(versions) == 27
            assert all(state == "ready" and count == 1 for state, count in versions)
        point_count = workspace.store.client.count(audit_config.name, exact=True).count
        assert point_count == 27
        before = bindings.checksum
        assert seed_workspace(workspace, template).checksum == before
        assert (
            workspace.store.client.count(audit_config.name, exact=True).count
            == point_count
        )
        serialized = audit_config.bindings_path.read_text(encoding="utf-8")
        assert "AuditFixturePasswordMarker" not in serialized
        assert all(canary not in serialized for canary in template.canaries.values())
    with AuditWorkspace(audit_config, template) as workspace:
        assert seed_workspace(workspace, template).checksum == before


def test_second_runner_cannot_share_a_workspace_lock(
    audit_config: AuditConfiguration,
) -> None:
    template = generate_fixtures()
    with AuditWorkspace(audit_config, template):
        with pytest.raises(AuditWorkspaceError, match="audit_workspace_busy"):
            with AuditWorkspace(audit_config, template):
                pytest.fail("concurrent audit acquired the same lock")


def test_normal_application_role_does_not_gain_audit_table_access(
    audit_config: AuditConfiguration,
) -> None:
    with AuditWorkspace(audit_config, generate_fixtures()) as workspace:
        with workspace.admin_engine.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = 'ragelit_app'")
            ).scalar_one_or_none()
            if exists:
                assert not connection.execute(
                    text(
                        "SELECT has_table_privilege("
                        "'ragelit_app', 'public.documents', 'SELECT')"
                    )
                ).scalar_one()


def test_unknown_database_rows_are_refused_before_another_mutation(
    audit_config: AuditConfiguration,
) -> None:
    with AuditWorkspace(audit_config, generate_fixtures()) as workspace:
        with workspace.admin_engine.begin() as connection:
            connection.execute(
                insert(User).values(
                    id=uuid4(),
                    email="unknown@example.invalid",
                    password_hash="not-a-password",
                    is_active=True,
                )
            )
        with pytest.raises(AuditWorkspaceError, match="audit_resource_drift"):
            with workspace.mutation():
                pytest.fail("mutation ran despite unknown resources")
        with workspace.admin_engine.connect() as connection:
            assert (
                connection.execute(text("SELECT count(*) FROM users")).scalar_one() == 1
            )


def test_application_role_must_not_own_protected_tables(
    audit_config: AuditConfiguration,
) -> None:
    with AuditWorkspace(audit_config, generate_fixtures()) as workspace:
        with workspace.admin_engine.begin() as connection:
            connection.execute(
                text(
                    f'ALTER TABLE documents OWNER TO "{audit_config.application_role}"'
                )
            )
        with pytest.raises(AuditWorkspaceError, match="unsafe_audit_role"):
            workspace.validate_owned()


def test_unknown_schema_is_not_adopted_by_an_owned_workspace(
    audit_config: AuditConfiguration,
) -> None:
    with AuditWorkspace(audit_config, generate_fixtures()) as workspace:
        with workspace.admin_engine.begin() as connection:
            connection.execute(text("CREATE SCHEMA unknown_resources"))
        with pytest.raises(AuditWorkspaceError, match="audit_resource_drift"):
            workspace.validate_owned()


def test_unknown_vector_points_are_refused_and_not_removed(
    audit_config: AuditConfiguration,
) -> None:
    with AuditWorkspace(audit_config, generate_fixtures()) as workspace:
        point_id = str(uuid4())
        workspace.store.client.upsert(
            audit_config.name,
            points=[
                models.PointStruct(
                    id=point_id,
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
            workspace.validate_owned()
        assert (
            len(workspace.store.client.retrieve(audit_config.name, ids=[point_id])) == 1
        )


def test_binding_or_template_drift_is_refused(audit_config: AuditConfiguration) -> None:
    with AuditWorkspace(audit_config, generate_fixtures()) as workspace:
        manifest = json.loads(audit_config.bindings_path.read_bytes())
        manifest["template_hash"] = "0" * 64
        audit_config.bindings_path.write_text(json.dumps(manifest), encoding="utf-8")
        with pytest.raises(AuditWorkspaceError, match="audit_marker_drift"):
            workspace.validate_owned()


def test_interrupted_instances_remain_incomplete_without_resetting_base(
    audit_config: AuditConfiguration,
) -> None:
    template = generate_fixtures()
    run_id = uuid4()
    with AuditWorkspace(audit_config, template) as workspace:
        workspace.begin_instance(run_id, "org-1:deleted")
    with AuditWorkspace(audit_config, template) as workspace:
        assert (
            workspace.bindings.instances[f"{run_id}:org-1:deleted"].state
            == "incomplete"
        )
        assert workspace.bindings.documents == {}


def test_existing_unowned_database_is_not_migrated_or_reset(
    audit_config: AuditConfiguration,
) -> None:
    url = make_url(audit_config.database_admin_url.get_secret_value())
    server = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with server.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{audit_config.name}"'))
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE unrelated_data (value text)"))
        connection.execute(text("INSERT INTO unrelated_data VALUES ('keep this')"))
    with pytest.raises(AuditWorkspaceError, match="unowned_audit_workspace"):
        with AuditWorkspace(audit_config, generate_fixtures()):
            pytest.fail("unowned target was accepted")
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT value FROM unrelated_data")).scalar_one()
            == "keep this"
        )
    engine.dispose()
    server.dispose()
