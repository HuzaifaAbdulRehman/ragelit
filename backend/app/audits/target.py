import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, cast
from uuid import UUID, uuid4

from fastapi import FastAPI
from sqlalchemy.orm import Session

from app.audits.contracts import AuditCase, AuditObservation
from app.audits.fixtures import FixtureDocument, LogicalCase
from app.audits.observer import AuditObserver
from app.audits.seeding import (
    FixtureCitingProvider,
    ingest_document,
    login_actor,
    seed_workspace,
    verify_probe,
)
from app.audits.workspace import (
    AuditWorkspace,
    AuditWorkspaceError,
    DocumentBinding,
    InstanceBinding,
)
from app.chat.contracts import Generation, GenerationProvider
from app.core.security import hash_password
from app.identity.models import User
from app.retrieval.contracts import AuthorizedChunk
from app.retrieval.store import QdrantChunkStore
from app.tenancy.enums import Role
from app.tenancy.models import Membership


@dataclass(frozen=True)
class PreparedRequest:
    question: str = field(repr=False)
    headers: Mapping[str, str] = field(repr=False)
    instance_key: str | None = None
    citation_challenge: UUID | None = None
    forge_metadata: bool = False


@dataclass(frozen=True)
class PreparedPack:
    run_id: UUID
    cases: tuple[AuditCase, ...]
    requests: Mapping[str, PreparedRequest] = field(repr=False)
    canaries: Mapping[str, str] = field(repr=False)
    known_chunk_ids: frozenset[UUID]


@dataclass(frozen=True)
class _Evidence:
    binding: DocumentBinding
    canary_id: str
    canary: str = field(repr=False)
    allowed_actors: frozenset[UUID]


def _instance_canary(key: str, *, replacement: bool = False) -> tuple[str, str]:
    identity = f"{key}:{replacement}"
    label_digest = hashlib.sha256(f"label:{identity}".encode()).hexdigest()
    value_digest = hashlib.sha256(f"value:{identity}".encode()).hexdigest()
    return f"instance-{label_digest[:32]}", f"AUDITCANARY{value_digest[:32]}"


def _instance_text(
    document: FixtureDocument, key: str, *, replacement: bool = False
) -> str:
    _, canary = _instance_canary(key, replacement=replacement)
    return document.text.replace(document.canary, canary)


def _update_instance(workspace: AuditWorkspace, key: str, **changes: Any) -> None:
    with workspace.mutation():
        instance = workspace.bindings.instances[key].model_copy(update=changes)
        workspace.bindings = workspace.bindings.model_copy(
            update={"instances": workspace.bindings.instances | {key: instance}}
        )


def _request(
    workspace: AuditWorkspace,
    method: str,
    path: str,
    expected_status: int,
    **kwargs: Any,
) -> dict[str, Any]:
    with workspace.mutation():
        response = workspace.client.request(method, path, **kwargs)
        if response.status_code != expected_status:
            raise AuditWorkspaceError("audit_action_failed")
        return cast(dict[str, Any], response.json()) if response.content else {}


def _prepare_instance(
    workspace: AuditWorkspace, run_id: UUID, logical: LogicalCase, password_hash: str
) -> tuple[PreparedRequest, DocumentBinding, UUID]:
    documents = {document.id: document for document in workspace.template.documents}
    document = documents[logical.document_id]
    org = next(
        org
        for org in workspace.template.organizations
        if org.id == document.organization_id
    )
    org_id = workspace.bindings.organizations[org.id]
    owner = next(
        actor for actor in workspace.template.actors if actor.id == f"{org.id}-owner"
    )
    manager = login_actor(workspace, email=owner.email, organization_slug=org.slug)
    key = workspace.begin_instance(run_id, logical.id)
    actor_id, membership_id = uuid4(), uuid4()
    email = f"instance-{actor_id.hex}@example.invalid"
    with workspace.mutation():
        with Session(workspace.admin_engine) as session:
            session.add(User(id=actor_id, email=email, password_hash=password_hash))
            session.flush()
            session.add(
                Membership(
                    id=membership_id,
                    user_id=actor_id,
                    organization_id=org_id,
                    role=Role.MEMBER,
                )
            )
            session.commit()
        workspace.bindings = workspace.bindings.model_copy(
            update={
                "instances": workspace.bindings.instances
                | {key: InstanceBinding(actor_id=actor_id, membership_id=membership_id)}
            }
        )
    actor_headers = login_actor(workspace, email=email, organization_slug=org.slug)
    group_id = None
    if logical.action == "revoke_group":
        group = _request(
            workspace,
            "POST",
            f"/api/v1/organizations/{org_id}/groups",
            201,
            headers=manager,
            json={"name": f"instance-{actor_id.hex}"},
        )
        group_id = UUID(group["id"])
        _update_instance(workspace, key, group_id=group_id)
        _request(
            workspace,
            "POST",
            f"/api/v1/organizations/{org_id}/groups/{group_id}/members/{membership_id}",
            204,
            headers=manager,
        )
    uploaded = _request(
        workspace,
        "POST",
        "/api/v1/documents",
        202,
        params={"filename": f"instance-{actor_id.hex}.txt"},
        headers=manager | {"Content-Type": "text/plain"},
        content=_instance_text(document, key).encode("utf-8"),
    )
    document_id = UUID(uploaded["id"])
    _request(
        workspace,
        "PATCH",
        f"/api/v1/documents/{document_id}",
        200,
        headers=manager,
        json={
            "visibility": "restricted",
            "user_ids": [] if group_id else [str(actor_id)],
            "group_ids": [str(group_id)] if group_id else [],
        },
    )
    binding = ingest_document(workspace, org_id, document_id)
    _update_instance(workspace, key, document=binding)
    verify_probe(workspace, actor_headers, document.question, binding)
    if logical.action == "revoke_membership":
        _request(
            workspace,
            "PATCH",
            f"/api/v1/organizations/{org_id}/members/{membership_id}/active",
            200,
            headers=manager,
            json={"is_active": False},
        )
    elif logical.action == "revoke_grant":
        _request(
            workspace,
            "PATCH",
            f"/api/v1/documents/{document_id}",
            200,
            headers=manager,
            json={"visibility": "restricted", "user_ids": [], "group_ids": []},
        )
    elif logical.action == "revoke_group":
        _request(
            workspace,
            "DELETE",
            f"/api/v1/organizations/{org_id}/groups/{group_id}/members/{membership_id}",
            204,
            headers=manager,
        )
    elif logical.action == "delete":
        _request(
            workspace,
            "DELETE",
            f"/api/v1/documents/{document_id}",
            204,
            headers=manager,
        )
    elif logical.action == "replace":
        _request(
            workspace,
            "POST",
            f"/api/v1/documents/{document_id}/versions",
            202,
            params={"filename": f"replacement-{actor_id.hex}.txt"},
            headers=manager | {"Content-Type": "text/plain"},
            content=_instance_text(document, key, replacement=True).encode("utf-8"),
        )
        current = ingest_document(workspace, org_id, document_id)
        verify_probe(workspace, actor_headers, document.question, current)
        _update_instance(workspace, key, document=current, previous=binding)
        if logical.positive:
            binding = current
    return (
        PreparedRequest(
            document.question, MappingProxyType(actor_headers), instance_key=key
        ),
        binding,
        actor_id,
    )


def _evidence_registry(workspace: AuditWorkspace) -> tuple[_Evidence, ...]:
    evidence: list[_Evidence] = []
    template = workspace.template
    logical_by_id = {logical.id: logical for logical in template.cases}
    documents_by_id = {document.id: document for document in template.documents}
    isolated_actors: dict[str, set[UUID]] = {}
    for key, instance in workspace.bindings.instances.items():
        _, logical_id = key.split(":", 1)
        logical = logical_by_id[logical_id]
        if instance.actor_id is None or instance.document is None:
            raise AuditWorkspaceError("audit_instance_incomplete")
        if logical.action != "revoke_membership":
            organization_id = documents_by_id[logical.document_id].organization_id
            isolated_actors.setdefault(organization_id, set()).add(instance.actor_id)
    for document in template.documents:
        allowed = frozenset(
            workspace.bindings.actors[actor.id]
            for actor in template.actors
            if actor.organization_id == document.organization_id
            and (
                document.visibility == "organization"
                or actor.id in document.user_ids
                or actor.group_id in document.group_ids
            )
        )
        if document.visibility == "organization":
            allowed |= frozenset(isolated_actors.get(document.organization_id, set()))
        evidence.append(
            _Evidence(
                workspace.bindings.documents[document.id],
                document.canary_id,
                document.canary,
                allowed,
            )
        )
    for key, instance in workspace.bindings.instances.items():
        _, logical_id = key.split(":", 1)
        logical = logical_by_id[logical_id]
        if instance.actor_id is None or instance.document is None:
            raise AuditWorkspaceError("audit_instance_incomplete")
        replacement = instance.previous is not None
        canary_id, canary = _instance_canary(key, replacement=replacement)
        allowed = (
            frozenset({instance.actor_id})
            if logical.action == "replace"
            else frozenset()
        )
        evidence.append(_Evidence(instance.document, canary_id, canary, allowed))
        if instance.previous is not None:
            canary_id, canary = _instance_canary(key)
            evidence.append(
                _Evidence(instance.previous, canary_id, canary, frozenset())
            )
    return tuple(evidence)


def prepare_pack(workspace: AuditWorkspace, run_id: UUID) -> PreparedPack:
    workspace.validate_owned()
    if any(
        instance.state == "incomplete"
        for instance in workspace.bindings.instances.values()
    ):
        raise AuditWorkspaceError("audit_instance_incomplete")
    seed_workspace(workspace, workspace.template)
    app = cast(FastAPI, workspace.client.app)
    app.state.generation_provider = FixtureCitingProvider()
    app.state.observation_sink = None
    actor_by_id = {actor.id: actor for actor in workspace.template.actors}
    document_by_id = {
        document.id: document for document in workspace.template.documents
    }
    org_by_id = {org.id: org for org in workspace.template.organizations}
    requests: dict[str, PreparedRequest] = {}
    required: dict[str, DocumentBinding] = {}
    actor_ids: dict[str, UUID] = {}
    password_hash = hash_password(workspace.config.fixture_password.get_secret_value())
    for logical in workspace.template.cases:
        if logical.action in {
            "revoke_membership",
            "revoke_grant",
            "revoke_group",
            "delete",
            "replace",
        }:
            request, binding, actor_id = _prepare_instance(
                workspace, run_id, logical, password_hash
            )
        else:
            actor = actor_by_id[logical.actor_id]
            headers = login_actor(
                workspace,
                email=actor.email,
                organization_slug=org_by_id[actor.organization_id].slug,
            )
            binding = workspace.bindings.documents[logical.document_id]
            challenge = (
                binding.chunk_ids[0] if logical.action == "challenge_citation" else None
            )
            request = PreparedRequest(
                document_by_id[logical.document_id].question,
                MappingProxyType(headers),
                citation_challenge=challenge,
                forge_metadata=logical.action == "forge_metadata",
            )
            actor_id = workspace.bindings.actors[logical.actor_id]
        requests[logical.id], required[logical.id], actor_ids[logical.id] = (
            request,
            binding,
            actor_id,
        )
    evidence = _evidence_registry(workspace)
    cases = tuple(
        AuditCase(
            id=logical.id,
            expected_status=logical.expected_status,
            expected_denial_code="membership_inactive"
            if logical.action == "revoke_membership"
            else None,
            positive=logical.positive,
            required_chunks=required[logical.id].chunk_ids if logical.positive else (),
            forbidden_chunks=tuple(
                chunk_id
                for item in evidence
                if actor_ids[logical.id] not in item.allowed_actors
                for chunk_id in item.binding.chunk_ids
            ),
            forbidden_canaries=tuple(
                item.canary_id
                for item in evidence
                if actor_ids[logical.id] not in item.allowed_actors
            ),
            citation_challenge=requests[logical.id].citation_challenge,
        )
        for logical in workspace.template.cases
    )
    return PreparedPack(
        run_id,
        cases,
        MappingProxyType(requests),
        MappingProxyType({item.canary_id: item.canary for item in evidence}),
        frozenset(chunk_id for item in evidence for chunk_id in item.binding.chunk_ids),
    )


class _ChallengeProvider:
    def __init__(self, challenge: UUID) -> None:
        self.challenge = challenge

    def generate(
        self, question: str, context: tuple[AuthorizedChunk, ...]
    ) -> Generation:
        generated = FixtureCitingProvider().generate(question, context)
        return Generation(generated.answer, generated.citation_ids + (self.challenge,))


class BundledAuditTarget:
    def __init__(
        self,
        workspace: AuditWorkspace,
        pack: PreparedPack | None,
        *,
        profile: str = "safe",
        lab: bool = False,
        generation_provider: GenerationProvider | None = None,
    ) -> None:
        if profile not in {"safe", "vulnerable", "deny_all"}:
            raise AuditWorkspaceError("invalid_audit_profile")
        if profile != "safe" and not lab:
            raise AuditWorkspaceError("audit_lab_opt_in_required")
        workspace.validate_owned()
        if pack is None:
            raise AuditWorkspaceError("audit_pack_missing")
        if generation_provider is not None and (
            profile != "safe"
            or any(
                request.citation_challenge is not None
                for request in pack.requests.values()
            )
        ):
            raise AuditWorkspaceError("audit_provider_conflict")
        self.workspace, self.pack, self.profile = workspace, pack, profile
        self._generation_provider = generation_provider
        self._cases = {case.id: case for case in pack.cases}
        self._executed: set[str] = set()
        self._last_observation: AuditObservation | None = None

    @property
    def last_observation(self) -> AuditObservation | None:
        return self._last_observation

    def execute(self, case: AuditCase) -> AuditObservation:
        self._last_observation = None
        if self._cases.get(case.id) != case or case.id in self._executed:
            raise AuditWorkspaceError("audit_case_mismatch")
        request = self.pack.requests[case.id]
        workspace = self.workspace
        app = cast(FastAPI, workspace.client.app)
        observer = AuditObserver(
            case_id=case.id,
            canaries=self.pack.canaries,
            known_chunk_ids=self.pack.known_chunk_ids,
        )
        provider = (
            self._generation_provider
            if self._generation_provider is not None
            else _ChallengeProvider(request.citation_challenge)
            if request.citation_challenge is not None
            else FixtureCitingProvider()
        )
        store = workspace.store
        if self.profile != "safe":
            from app.audits.lab import LabQueryClient

            store = QdrantChunkStore(
                cast(Any, LabQueryClient(workspace, self.profile, lab=True)),
                workspace.store.collection_name,
                dimension=workspace.store.dimension,
            )
        command: dict[str, Any] = {"question": request.question, "limit": 20}
        if request.forge_metadata:
            command.update(
                {"organization_id": str(uuid4()), "role": "owner", "group_ids": []}
            )
        previous_provider = getattr(app.state, "generation_provider", None)
        previous_observer = getattr(app.state, "observation_sink", None)
        previous_store = getattr(app.state, "chunk_store", None)
        (
            app.state.generation_provider,
            app.state.observation_sink,
            app.state.chunk_store,
        ) = provider, observer, store
        try:
            with workspace.mutation():
                response = workspace.client.post(
                    "/api/v1/chat/query",
                    headers=request.headers,
                    json=command,
                )
                if response.status_code in {401, 403, 422}:
                    code = response.json().get("code")
                    observer.finish(
                        response.status_code,
                        "failed",
                        code if isinstance(code, str) else None,
                    )
                if request.instance_key is not None:
                    instance = workspace.bindings.instances[
                        request.instance_key
                    ].model_copy(update={"state": "complete"})
                    workspace.bindings = workspace.bindings.model_copy(
                        update={
                            "instances": workspace.bindings.instances
                            | {request.instance_key: instance}
                        }
                    )
            self._executed.add(case.id)
            return observer.snapshot()
        finally:
            try:
                self._last_observation = observer.snapshot()
            finally:
                app.state.generation_provider = previous_provider
                app.state.observation_sink = previous_observer
                app.state.chunk_store = previous_store
