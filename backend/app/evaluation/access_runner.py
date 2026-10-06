from collections.abc import Callable
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Literal, cast

from fastapi import FastAPI

from app.audits.contracts import AuditCase, Terminal
from app.audits.fixtures import generate_fixtures
from app.audits.isolation_lab import LabPostFilterStore
from app.audits.isolation_reports import IsolationStrategy
from app.audits.observer import AuditObserver
from app.audits.seeding import FixtureCitingProvider, login_actor
from app.audits.target import BundledAuditTarget, PreparedPack, _evidence_registry
from app.audits.workspace import AuditWorkspace, AuditWorkspaceError
from app.chat.contracts import GenerationProvider
from app.evaluation.access_registry import access_registry
from app.evaluation.access_reports import AccessObservation
from app.evaluation.models import load_embedding_pins
from app.evaluation.reports import GenerationRecord
from app.retrieval.store import QdrantChunkStore


@dataclass(frozen=True, slots=True)
class AccessCapture:
    record: AccessObservation
    runtime_failed: bool = False
    interrupted: bool = False


@dataclass(frozen=True, slots=True)
class AccessExecution:
    records: tuple[AccessObservation, ...]
    runtime_failed: bool
    interrupted: bool


def _checked_pack(workspace: AuditWorkspace, pack: PreparedPack) -> None:
    template = generate_fixtures()
    registry = access_registry(workspace.bindings, pack.run_id)
    if (
        workspace.template.checksum != template.checksum
        or pack.cases != registry.cases
        or set(pack.requests) != {case.id for case in registry.cases}
        or pack.known_chunk_ids != registry.known_chunk_ids
    ):
        raise ValueError("access_benchmark_pack_mismatch")
    evidence = _evidence_registry(workspace)
    if dict(pack.canaries) != {item.canary_id: item.canary for item in evidence}:
        raise ValueError("access_benchmark_registry_mismatch")
    cases = {case.id: case for case in registry.cases}
    documents = {doc.id: doc for doc in template.documents}
    for logical in template.cases:
        request = pack.requests[logical.id]
        key = f"{pack.run_id}:{logical.id}"
        if (
            request.question != documents[logical.document_id].question
            or request.citation_challenge != cases[logical.id].citation_challenge
            or request.instance_key
            != (key if key in workspace.bindings.instances else None)
            or request.forge_metadata != (logical.action == "forge_metadata")
        ):
            raise ValueError("access_benchmark_request_mismatch")


def capture_access_query(
    workspace: AuditWorkspace,
    pack: PreparedPack,
    case: AuditCase,
    *,
    provider: GenerationProvider,
    provider_profile: Literal["fixture", "local"],
    store: QdrantChunkStore | None = None,
) -> AccessCapture:
    case = AuditCase.model_validate(case.model_dump())
    workspace.validate_owned()
    _checked_pack(workspace, pack)
    if case not in pack.cases or provider_profile not in {"fixture", "local"}:
        raise ValueError("access_benchmark_case_mismatch")
    selected = workspace.store if store is None else store
    if (
        selected.client is not workspace.store.client
        or selected.dimension != workspace.store.dimension
        or selected.collection_names() != workspace.store.collection_names()
        or selected is not workspace.store
        and (
            not isinstance(selected, LabPostFilterStore)
            or selected.workspace is not workspace
        )
    ):
        raise ValueError("access_benchmark_store_mismatch")
    logical = next(item for item in workspace.template.cases if item.id == case.id)
    request = pack.requests[case.id]
    target: BundledAuditTarget | None = None
    failed = interrupted = False
    fallback = AuditObserver(
        case_id=case.id, canaries=pack.canaries, known_chunk_ids=pack.known_chunk_ids
    )
    try:
        if logical.action != "revoke_membership":
            actor = next(
                item
                for item in workspace.template.actors
                if item.id == logical.actor_id
            )
            organization = next(
                item
                for item in workspace.template.organizations
                if item.id == actor.organization_id
            )
            if request.instance_key is not None:
                actor_id = workspace.bindings.instances[request.instance_key].actor_id
                if actor_id is None:
                    raise ValueError("access_benchmark_actor_mismatch")
                email = f"instance-{actor_id.hex}@example.invalid"
            else:
                email = actor.email
            headers = login_actor(
                workspace, email=email, organization_slug=organization.slug
            )
            request = replace(request, headers=MappingProxyType(headers))
        selected_pack = replace(
            pack, cases=(case,), requests=MappingProxyType({case.id: request})
        )
        target = BundledAuditTarget(
            workspace,
            selected_pack,
            generation_provider=None
            if case.citation_challenge is not None
            else provider,
            lab=selected is not workspace.store,
            retrieval_store=selected if selected is not workspace.store else None,
        )
        observation = target.execute(case)
    except (Exception, KeyboardInterrupt) as error:
        failed = True
        interrupted = isinstance(error, KeyboardInterrupt)
        fallback.finish(
            401
            if isinstance(error, AuditWorkspaceError)
            and error.args == ("audit_login_failed",)
            else 503,
            "failed",
        )
        last = target.last_observation if target is not None else None
        observation = (
            last
            if last is not None and last.case_id == case.id
            else fallback.snapshot()
        )
    failed |= observation.terminal in {
        Terminal.RUNTIME_FAILED,
        Terminal.OBSERVER_FAILED,
    } or (
        observation.terminal
        in {Terminal.AUTHENTICATION_DENIED, Terminal.VALIDATION_DENIED}
        and (
            observation.http_status != case.expected_status
            or observation.denial_code != case.expected_denial_code
        )
    )
    record = AccessObservation(
        observation=observation,
        provider_profile="controlled_citation"
        if case.citation_challenge is not None
        else provider_profile,
    )
    return AccessCapture(record, failed, interrupted)


def execute_access_cohort(
    cases: tuple[AuditCase, ...],
    capture: Callable[[AuditCase], AccessCapture],
    *,
    on_record: Callable[[AccessExecution], None] | None = None,
) -> AccessExecution:
    cases = tuple(AuditCase.model_validate(case.model_dump()) for case in cases)
    identifiers = tuple(case.id for case in cases)
    if not cases or len(cases) > 51 or len(set(identifiers)) != len(cases):
        raise ValueError("access_benchmark_case_mismatch")
    records: list[AccessObservation] = []
    failed = interrupted = False
    for case in cases:
        try:
            captured = capture(case)
            record = AccessObservation.model_validate(captured.record.model_dump())
            if record.observation.case_id != case.id:
                raise ValueError("access_benchmark_case_mismatch")
            records.append(record)
            failed |= captured.runtime_failed or captured.interrupted
            interrupted |= captured.interrupted
            if on_record is not None:
                on_record(AccessExecution(tuple(records), failed, interrupted))
            if interrupted:
                break
        except (Exception, KeyboardInterrupt) as error:
            failed = True
            interrupted |= isinstance(error, KeyboardInterrupt)
            break
    return AccessExecution(tuple(records), failed, interrupted)


def execute_access_benchmark(
    workspace: AuditWorkspace,
    pack: PreparedPack,
    generation: GenerationRecord,
    *,
    strategy: IsolationStrategy,
    lab: bool = False,
    on_record: Callable[[AccessExecution], None] | None = None,
) -> AccessExecution:
    generation = GenerationRecord.model_validate(generation.model_dump())
    expected_strategy = (
        "tenant_collections"
        if strategy == "tenant_collections"
        else "shared_pre_filter"
    )
    if (
        workspace.config.embedding_fingerprint != load_embedding_pins().fingerprint
        or workspace.config.embedding_dimension != 384
        or getattr(workspace.embeddings, "fingerprint", None)
        != workspace.config.embedding_fingerprint
        or workspace.embeddings.dimension != 384
        or cast(FastAPI, workspace.client.app).state.embeddings
        is not workspace.embeddings
        or workspace.config.vector_strategy != expected_strategy
        or strategy
        not in {"shared_pre_filter", "tenant_collections", "lab_post_filter"}
        or lab != (strategy == "lab_post_filter")
    ):
        raise ValueError("access_benchmark_configuration_mismatch")
    workspace.validate_owned()
    _checked_pack(workspace, pack)
    provider: GenerationProvider = (
        generation.local.provider(workspace.settings)
        if generation.local is not None
        else FixtureCitingProvider()
    )
    store = LabPostFilterStore(workspace, lab=True) if lab else workspace.store
    return execute_access_cohort(
        pack.cases,
        lambda case: capture_access_query(
            workspace,
            pack,
            case,
            provider=provider,
            provider_profile=generation.mode,
            store=store,
        ),
        on_record=on_record,
    )
