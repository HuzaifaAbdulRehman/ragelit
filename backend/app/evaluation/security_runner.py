from collections.abc import Callable
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Literal, cast

from fastapi import FastAPI

from app.audits.contracts import AuditObservation, Terminal
from app.audits.injection_fixtures import (
    fact_marker,
    generate_injection_fixtures,
    injection_cases,
)
from app.audits.injection_providers import InjectionProvider
from app.audits.injection_reports import expected_injection_cases
from app.audits.injection_scoring import InjectionCase
from app.audits.isolation_lab import LabPostFilterStore
from app.audits.isolation_reports import IsolationStrategy
from app.audits.observer import AuditObserver
from app.audits.seeding import login_actor
from app.audits.target import BundledAuditTarget, PreparedPack
from app.audits.workspace import AuditWorkspace, AuditWorkspaceError
from app.chat.contracts import GenerationProvider
from app.evaluation.models import load_embedding_pins
from app.evaluation.reports import GenerationRecord
from app.retrieval.store import QdrantChunkStore


@dataclass(frozen=True, slots=True)
class InjectionCapture:
    observation: AuditObservation
    runtime_failed: bool = False
    interrupted: bool = False


@dataclass(frozen=True, slots=True)
class InjectionExecution:
    observations: tuple[AuditObservation, ...]
    runtime_failed: bool
    interrupted: bool


def _checked_registry(workspace: AuditWorkspace, pack: PreparedPack) -> None:
    template = workspace.template
    known_canaries = template.canaries | dict(
        fact_marker(template.seed, document.id) for document in template.documents
    )
    known_chunks = frozenset(
        chunk
        for binding in workspace.bindings.documents.values()
        for chunk in binding.chunk_ids
    )
    if dict(pack.canaries) != known_canaries or pack.known_chunk_ids != known_chunks:
        raise ValueError("injection_benchmark_registry_mismatch")


def capture_injection_query(
    workspace: AuditWorkspace,
    pack: PreparedPack,
    case: InjectionCase,
    *,
    provider: GenerationProvider,
    store: QdrantChunkStore | None = None,
) -> InjectionCapture:
    case = InjectionCase.model_validate(case.model_dump())
    if (
        workspace.template.pack_id != "injection-v1"
        or not workspace.bindings.seeded
        or case not in injection_cases(pack, workspace.template)
    ):
        raise ValueError("injection_benchmark_case_mismatch")
    workspace.validate_owned()
    _checked_registry(workspace, pack)
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
        raise ValueError("injection_benchmark_store_mismatch")
    logical = next(
        item for item in workspace.template.cases if item.id == case.access_case.id
    )
    actor = next(
        item for item in workspace.template.actors if item.id == logical.actor_id
    )
    organization = next(
        item
        for item in workspace.template.organizations
        if item.id == actor.organization_id
    )
    target: BundledAuditTarget | None = None
    interrupted = failed = False
    fallback = AuditObserver(
        case_id=case.access_case.id,
        canaries=pack.canaries,
        known_chunk_ids=pack.known_chunk_ids,
    )
    try:
        headers = login_actor(
            workspace, email=actor.email, organization_slug=organization.slug
        )
        request = replace(
            pack.requests[case.access_case.id], headers=MappingProxyType(headers)
        )
        fresh = replace(
            pack,
            requests=MappingProxyType(
                dict(pack.requests) | {case.access_case.id: request}
            ),
        )
        target = BundledAuditTarget(
            workspace,
            fresh,
            generation_provider=provider,
            lab=selected is not workspace.store,
            retrieval_store=selected if selected is not workspace.store else None,
        )
        observation = target.execute(case.access_case)
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
            if last is not None and last.case_id == case.access_case.id
            else fallback.snapshot()
        )
    observation = AuditObservation.model_validate(observation.model_dump())
    failed |= observation.terminal in {
        Terminal.RUNTIME_FAILED,
        Terminal.OBSERVER_FAILED,
        Terminal.AUTHENTICATION_DENIED,
        Terminal.VALIDATION_DENIED,
    }
    return InjectionCapture(observation, failed, interrupted)


def execute_injection_cohort(
    cases: tuple[InjectionCase, ...],
    capture: Callable[[InjectionCase], InjectionCapture],
    *,
    on_record: Callable[[InjectionExecution], None] | None = None,
) -> InjectionExecution:
    cases = tuple(InjectionCase.model_validate(case.model_dump()) for case in cases)
    identifiers = tuple(case.access_case.id for case in cases)
    if not cases or len(cases) > 120 or len(set(identifiers)) != len(cases):
        raise ValueError("injection_benchmark_case_mismatch")
    records: list[AuditObservation] = []
    failed = interrupted = False
    for case in cases:
        try:
            captured = capture(case)
            observation = AuditObservation.model_validate(
                captured.observation.model_dump()
            )
            if observation.case_id != case.access_case.id:
                raise ValueError("injection_benchmark_case_mismatch")
            records.append(observation)
            failed |= captured.runtime_failed
            interrupted |= captured.interrupted
            if on_record is not None:
                on_record(InjectionExecution(tuple(records), failed, interrupted))
            if interrupted:
                failed = True
                break
        except (Exception, KeyboardInterrupt) as error:
            failed = True
            interrupted |= isinstance(error, KeyboardInterrupt)
            break
    return InjectionExecution(tuple(records), failed, interrupted)


def execute_injection_benchmark(
    workspace: AuditWorkspace,
    pack: PreparedPack,
    generation: GenerationRecord,
    *,
    strategy: IsolationStrategy,
    lab: bool = False,
    trials: int = 1,
    provider_profile: str = "resistant",
    on_record: Callable[[InjectionExecution], None] | None = None,
) -> InjectionExecution:
    template = generate_injection_fixtures(trials=trials)
    generation = GenerationRecord.model_validate(generation.model_dump())
    if (
        workspace.template.checksum != template.checksum
        or workspace.config.embedding_fingerprint != load_embedding_pins().fingerprint
        or workspace.config.embedding_dimension != 384
        or getattr(workspace.embeddings, "fingerprint", None)
        != workspace.config.embedding_fingerprint
        or workspace.embeddings.dimension != 384
        or cast(FastAPI, workspace.client.app).state.embeddings
        is not workspace.embeddings
    ):
        raise ValueError("injection_benchmark_configuration_mismatch")
    expected_strategy = (
        "tenant_collections"
        if strategy == "tenant_collections"
        else "shared_pre_filter"
    )
    if (
        workspace.config.vector_strategy != expected_strategy
        or lab != (strategy == "lab_post_filter")
        or strategy
        not in {"shared_pre_filter", "tenant_collections", "lab_post_filter"}
        or provider_profile not in {"resistant", "obeying", "deny_all", "local"}
        or (provider_profile == "local") != (generation.mode == "local")
    ):
        raise ValueError("injection_benchmark_configuration_mismatch")
    workspace.validate_owned()
    _checked_registry(workspace, pack)
    cases = injection_cases(pack, template)
    if cases != expected_injection_cases(workspace.bindings.documents, trials=trials):
        raise ValueError("injection_benchmark_case_mismatch")
    provider: GenerationProvider
    if generation.local is not None:
        provider = generation.local.provider(workspace.settings)
    else:
        provider = InjectionProvider(
            cast(Literal["resistant", "obeying", "deny_all"], provider_profile)
        )
    store = LabPostFilterStore(workspace, lab=True) if lab else workspace.store
    return execute_injection_cohort(
        cases,
        lambda case: capture_injection_query(
            workspace, pack, case, provider=provider, store=store
        ),
        on_record=on_record,
    )
