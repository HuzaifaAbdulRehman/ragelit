from pathlib import Path
from uuid import uuid4

from app.audits.fixtures import generate_fixtures
from app.audits.target import _evidence_registry, _instance_canary
from app.audits.workspace import (
    AuditConfiguration,
    AuditWorkspace,
    DocumentBinding,
    InstanceBinding,
)


def test_instance_canary_identifier_does_not_encode_the_matched_value() -> None:
    key = "a-run:org-1:grant-revoked"
    identifier, value = _instance_canary(key)
    assert value.removeprefix("AUDITCANARY") not in identifier
    assert _instance_canary(key) == (identifier, value)
    assert _instance_canary(key, replacement=True) != (identifier, value)


def test_isolated_actor_keeps_only_its_organization_anchor_access(
    tmp_path: Path,
) -> None:
    template = generate_fixtures()
    name = "ragelit_audit_labels"
    config = AuditConfiguration.model_validate(
        {
            "environment": "test",
            "database_admin_url": f"postgresql+psycopg://postgres:postgres@127.0.0.1/{name}",
            "qdrant_url": "http://127.0.0.1:6333",
            "root": tmp_path / name,
            "application_password": "audit-label-application-password",
            "fixture_password": "audit-label-fixture-password",
        }
    )
    workspace = AuditWorkspace(config, template)
    documents = {
        document.id: DocumentBinding(
            document_id=uuid4(),
            version_id=uuid4(),
            chunk_ids=(uuid4(),),
            content_hash="a" * 64,
        )
        for document in template.documents
    }
    isolated_actor = uuid4()
    isolated_binding = DocumentBinding(
        document_id=uuid4(),
        version_id=uuid4(),
        chunk_ids=(uuid4(),),
        content_hash="b" * 64,
    )
    workspace.bindings = workspace.bindings.model_copy(
        update={
            "actors": {actor.id: uuid4() for actor in template.actors},
            "documents": documents,
            "instances": {
                f"{uuid4()}:org-1:grant-revoked": InstanceBinding(
                    state="complete",
                    actor_id=isolated_actor,
                    membership_id=uuid4(),
                    document=isolated_binding,
                )
            },
        }
    )
    evidence = {
        item.binding.document_id: item for item in _evidence_registry(workspace)
    }
    assert (
        isolated_actor in evidence[documents["org-1-anchor"].document_id].allowed_actors
    )
    assert (
        isolated_actor
        not in evidence[documents["org-2-anchor"].document_id].allowed_actors
    )
    assert (
        isolated_actor
        not in evidence[documents["org-1-user"].document_id].allowed_actors
    )
    assert isolated_actor not in evidence[isolated_binding.document_id].allowed_actors
