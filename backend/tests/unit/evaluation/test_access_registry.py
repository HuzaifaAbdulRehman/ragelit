import hashlib
from uuid import UUID, uuid5

import pytest

from app.audits.fixtures import FixtureDocument, generate_fixtures
from app.audits.workspace import DocumentBinding, FixtureBindings, InstanceBinding

RUN_ID = UUID(int=90000)


def access_inventory() -> FixtureBindings:
    template = generate_fixtures()
    documents = {
        document.id: DocumentBinding(
            document_id=UUID(int=100 + index),
            version_id=UUID(int=1000 + index),
            chunk_ids=(uuid5(UUID(int=1000 + index), "0"),),
            content_hash=hashlib.sha256(document.text.encode()).hexdigest(),
        )
        for index, document in enumerate(template.documents)
    }
    by_id = {document.id: document for document in template.documents}
    instances = {}
    actions = {"revoke_membership", "revoke_grant", "revoke_group", "delete", "replace"}
    for index, logical in enumerate(template.cases):
        if logical.action not in actions:
            continue
        key = f"{RUN_ID}:{logical.id}"
        original = by_id[logical.document_id]

        def binding(
            replacement: bool,
            key: str = key,
            original: FixtureDocument = original,
            index: int = index,
        ) -> DocumentBinding:
            identity = f"{key}:True" if replacement else f"{key}:False"
            marker = (
                "AUDITCANARY"
                + hashlib.sha256(f"value:{identity}".encode()).hexdigest()[:32]
            )
            text = original.text.replace(original.canary, marker)
            version = UUID(int=(60000 if replacement else 50000) + index)
            return DocumentBinding(
                document_id=UUID(int=40000 + index),
                version_id=version,
                chunk_ids=(uuid5(version, "0"),),
                content_hash=hashlib.sha256(text.encode()).hexdigest(),
            )

        instances[key] = InstanceBinding(
            actor_id=UUID(int=10000 + index),
            membership_id=UUID(int=20000 + index),
            group_id=UUID(int=30000 + index)
            if logical.action == "revoke_group"
            else None,
            document=binding(logical.action == "replace"),
            previous=binding(False) if logical.action == "replace" else None,
        )
    return FixtureBindings(
        workspace_id=UUID(int=80000),
        template_hash=template.checksum,
        collection="ragelit_audit_unit",
        seeded=True,
        organizations={
            item.id: UUID(int=70000 + index)
            for index, item in enumerate(template.organizations)
        },
        actors={
            item.id: UUID(int=1 + index) for index, item in enumerate(template.actors)
        },
        documents=documents,
        instances=instances,
    )


def test_access_registry_replays_permissions_versions_and_citation_challenge() -> None:
    from app.evaluation.access_registry import access_registry

    inventory = access_inventory()
    result = access_registry(inventory, RUN_ID)
    cases = {case.id: case for case in result.cases}
    assert len(cases) == 51
    assert sum(case.positive for case in cases.values()) == 12
    assert len(result.known_chunk_ids) == 51
    assert len(result.known_canary_ids) == 51
    public = inventory.documents["org-1-organization"].chunk_ids[0]
    private = inventory.documents["org-1-user"].chunk_ids[0]
    other_org = inventory.documents["org-2-organization"].chunk_ids[0]
    assert public in cases["org-1:organization"].required_chunks
    assert public not in cases["org-1:organization"].forbidden_chunks
    assert other_org in cases["org-1:organization"].forbidden_chunks
    assert private not in cases["org-1:user-allowed"].forbidden_chunks
    assert private in cases["org-1:user-denied"].forbidden_chunks
    assert private in cases["org-1:role-owner"].forbidden_chunks
    assert public in cases["org-1:membership-revoked"].forbidden_chunks
    assert (
        cases["org-1:membership-revoked"].expected_denial_code == "membership_inactive"
    )
    old = inventory.instances[f"{RUN_ID}:org-1:version-old"]
    new = inventory.instances[f"{RUN_ID}:org-1:version-new"]
    assert (
        old.previous is not None
        and new.previous is not None
        and new.document is not None
    )
    assert old.previous.chunk_ids[0] in cases["org-1:version-old"].forbidden_chunks
    assert new.previous.chunk_ids[0] in cases["org-1:version-new"].forbidden_chunks
    assert new.document.chunk_ids == cases["org-1:version-new"].required_chunks
    assert new.document.chunk_ids[0] not in cases["org-1:version-new"].forbidden_chunks
    challenge = inventory.documents["org-1-ungranted"].chunk_ids[0]
    assert cases["org-1:citation"].citation_challenge == challenge
    assert challenge in cases["org-1:citation"].forbidden_chunks


@pytest.mark.parametrize(
    "drift",
    [
        "template",
        "seeded",
        "actor",
        "document",
        "chunk",
        "content",
        "instance",
        "key",
        "instance_actor",
        "previous",
        "group",
    ],
)
def test_access_registry_rejects_unbound_or_mutated_inventory(drift: str) -> None:
    from app.evaluation.access_registry import access_registry

    inventory = access_inventory()
    if drift == "template":
        inventory = inventory.model_copy(update={"template_hash": "a" * 64})
    elif drift == "seeded":
        inventory = inventory.model_copy(update={"seeded": False})
    elif drift == "actor":
        inventory.actors.pop("org-1-owner")
    elif drift == "document":
        inventory.documents.pop("org-1-user")
    elif drift in {"chunk", "content"}:
        original = inventory.documents["org-1-user"]
        inventory.documents["org-1-user"] = original.model_copy(
            update={
                "chunk_ids": (UUID(int=99),)
                if drift == "chunk"
                else original.chunk_ids,
                "content_hash": "a" * 64
                if drift == "content"
                else original.content_hash,
            }
        )
    else:
        key = f"{RUN_ID}:org-1:version-new"
        instance = inventory.instances[key]
        if drift == "instance":
            inventory.instances.pop(key)
        elif drift == "key":
            inventory.instances["invalid:org-1:version-new"] = inventory.instances.pop(
                key
            )
        else:
            updates: dict[str, dict[str, object]] = {
                "instance_actor": {"actor_id": inventory.actors["org-1-owner"]},
                "previous": {"previous": None},
                "group": {"group_id": UUID(int=99)},
            }
            inventory.instances[key] = instance.model_copy(update=updates[drift])
    with pytest.raises(ValueError, match="access_benchmark_inventory_invalid"):
        access_registry(inventory, RUN_ID)
