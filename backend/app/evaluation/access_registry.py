import hashlib
from dataclasses import dataclass
from uuid import UUID, uuid5

from app.audits.contracts import AuditCase
from app.audits.fixtures import generate_fixtures
from app.audits.target import _instance_canary, _instance_text
from app.audits.workspace import DocumentBinding, FixtureBindings

_ERROR = "access_benchmark_inventory_invalid"
_ACTIONS = {"revoke_membership", "revoke_grant", "revoke_group", "delete", "replace"}


@dataclass(frozen=True, slots=True)
class AccessRegistry:
    cases: tuple[AuditCase, ...]
    known_chunk_ids: frozenset[UUID]
    known_canary_ids: frozenset[str]


def _checked_document(binding: DocumentBinding, text: str) -> None:
    if binding.content_hash != hashlib.sha256(
        text.encode()
    ).hexdigest() or binding.chunk_ids != (uuid5(binding.version_id, "0"),):
        raise ValueError(_ERROR)


def access_registry(inventory: FixtureBindings, run_id: UUID) -> AccessRegistry:
    inventory = FixtureBindings.model_validate(inventory.model_dump())
    template = generate_fixtures()
    documents = {item.id: item for item in template.documents}
    logical = {item.id: item for item in template.cases}
    expected_instances = {
        f"{run_id}:{item.id}" for item in template.cases if item.action in _ACTIONS
    }
    if (
        not isinstance(run_id, UUID)
        or not inventory.seeded
        or inventory.template_hash != template.checksum
        or set(inventory.documents) != set(documents)
        or set(inventory.actors) != {actor.id for actor in template.actors}
        or set(inventory.organizations) != {org.id for org in template.organizations}
        or len(set(inventory.organizations.values())) != 3
        or len(set(inventory.actors.values())) != len(template.actors)
        or not expected_instances.issubset(inventory.instances)
        or len(inventory.instances) > 500
    ):
        raise ValueError(_ERROR)
    document_ids: set[UUID] = set()
    version_ids: set[UUID] = set()
    actor_ids = set(inventory.actors.values())
    isolated: dict[str, set[UUID]] = {}
    for item in template.documents:
        binding = inventory.documents[item.id]
        _checked_document(binding, item.text)
        if binding.document_id in document_ids or binding.version_id in version_ids:
            raise ValueError(_ERROR)
        document_ids.add(binding.document_id)
        version_ids.add(binding.version_id)
    for key, instance in inventory.instances.items():
        prefix, separator, identifier = key.partition(":")
        try:
            valid_key = separator and prefix == str(UUID(prefix))
        except ValueError:
            valid_key = False
        case = logical.get(identifier)
        if (
            not valid_key
            or case is None
            or case.action not in _ACTIONS
            or instance.actor_id is None
            or instance.membership_id is None
            or instance.document is None
            or instance.actor_id in actor_ids
            or instance.state == "skipped"
            or (instance.previous is not None) != (case.action == "replace")
            or (instance.group_id is not None) != (case.action == "revoke_group")
        ):
            raise ValueError(_ERROR)
        actor_ids.add(instance.actor_id)
        original = documents[case.document_id]
        if instance.document.document_id in document_ids:
            raise ValueError(_ERROR)
        document_ids.add(instance.document.document_id)
        _checked_document(
            instance.document,
            _instance_text(original, key, replacement=case.action == "replace"),
        )
        bindings: tuple[DocumentBinding, ...] = (instance.document,)
        if instance.previous is not None:
            if instance.previous.document_id != instance.document.document_id:
                raise ValueError(_ERROR)
            _checked_document(instance.previous, _instance_text(original, key))
            bindings += (instance.previous,)
        for binding in bindings:
            if binding.version_id in version_ids:
                raise ValueError(_ERROR)
            version_ids.add(binding.version_id)
        if case.action != "revoke_membership":
            isolated.setdefault(original.organization_id, set()).add(instance.actor_id)
    evidence: list[tuple[DocumentBinding, str, frozenset[UUID]]] = []
    for item in template.documents:
        allowed = frozenset(
            inventory.actors[actor.id]
            for actor in template.actors
            if actor.organization_id == item.organization_id
            and (
                item.visibility == "organization"
                or actor.id in item.user_ids
                or actor.group_id in item.group_ids
            )
        )
        if item.visibility == "organization":
            allowed |= frozenset(isolated.get(item.organization_id, set()))
        evidence.append((inventory.documents[item.id], item.canary_id, allowed))
    for key, instance in inventory.instances.items():
        case = logical[key.partition(":")[2]]
        assert instance.actor_id is not None and instance.document is not None
        replaced = instance.previous is not None
        marker, _ = _instance_canary(key, replacement=replaced)
        allowed = (
            frozenset({instance.actor_id}) if case.action == "replace" else frozenset()
        )
        evidence.append((instance.document, marker, allowed))
        if instance.previous is not None:
            marker, _ = _instance_canary(key)
            evidence.append((instance.previous, marker, frozenset()))
    cases = []
    for logical_case in template.cases:
        case_instance = inventory.instances.get(f"{run_id}:{logical_case.id}")
        if logical_case.action in _ACTIONS:
            assert case_instance is not None and case_instance.actor_id is not None
            actor_id = case_instance.actor_id
            case_binding = (
                case_instance.document
                if logical_case.positive
                else case_instance.previous or case_instance.document
            )
            assert case_binding is not None
        else:
            actor_id = inventory.actors[logical_case.actor_id]
            case_binding = inventory.documents[logical_case.document_id]
        cases.append(
            AuditCase(
                id=logical_case.id,
                expected_status=logical_case.expected_status,
                expected_denial_code="membership_inactive"
                if logical_case.action == "revoke_membership"
                else None,
                positive=logical_case.positive,
                required_chunks=case_binding.chunk_ids if logical_case.positive else (),
                forbidden_chunks=tuple(
                    chunk
                    for source, _, allowed in evidence
                    if actor_id not in allowed
                    for chunk in source.chunk_ids
                ),
                forbidden_canaries=tuple(
                    marker for _, marker, allowed in evidence if actor_id not in allowed
                ),
                citation_challenge=case_binding.chunk_ids[0]
                if logical_case.action == "challenge_citation"
                else None,
            )
        )
    return AccessRegistry(
        tuple(cases),
        frozenset(chunk for binding, _, _ in evidence for chunk in binding.chunk_ids),
        frozenset(marker for _, marker, _ in evidence),
    )
