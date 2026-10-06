import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast, get_args

from fastapi import FastAPI

from app.audits.contracts import Boundary, Terminal
from app.audits.isolation_lab import LabPostFilterStore
from app.audits.isolation_reports import IsolationStrategy
from app.audits.observer import AuditObserver
from app.audits.seeding import FixtureCitingProvider, login_actor
from app.audits.workspace import AuditWorkspace, AuditWorkspaceError
from app.chat.contracts import GenerationProvider
from app.chat.schemas import AnswerResponse
from app.evaluation.dataset import CorpusQuery, UtilityCorpus, generate_utility_corpus
from app.evaluation.models import load_embedding_pins
from app.evaluation.reports import (
    CitationRecord,
    ErrorCode,
    GenerationRecord,
    UtilityDocumentBinding,
    UtilityQueryRecord,
)
from app.evaluation.workspace import utility_template
from app.retrieval.store import QdrantChunkStore


@dataclass(frozen=True, slots=True)
class QueryCapture:
    record: UtilityQueryRecord
    interrupted: bool


@dataclass(frozen=True, slots=True)
class UtilityExecution:
    records: tuple[UtilityQueryRecord, ...]
    runtime_failed: bool
    interrupted: bool


def answer_label_matches(answer: str | None, expected: str) -> bool:
    if answer is None:
        return False
    normalized = " ".join(answer.split()).casefold()
    label = " ".join(expected.split()).casefold()
    if not label:
        raise ValueError("utility_answer_label_missing")
    return (
        re.search(r"(?<![\w.])" + re.escape(label) + r"(?!\w)", normalized) is not None
    )


def _corpus(corpus: UtilityCorpus) -> UtilityCorpus:
    corpus = UtilityCorpus.model_validate(corpus.model_dump())
    if corpus.seed != 20261005 or corpus.checksum != generate_utility_corpus().checksum:
        raise ValueError("utility_corpus_drift")
    return corpus


def utility_document_bindings(
    workspace: AuditWorkspace, corpus: UtilityCorpus
) -> tuple[UtilityDocumentBinding, ...]:
    corpus = _corpus(corpus)
    if not workspace.bindings.seeded or set(workspace.bindings.documents) != {
        document.id for document in corpus.documents
    }:
        raise ValueError("utility_inventory_incomplete")
    return tuple(
        UtilityDocumentBinding(
            logical_id=document.id,
            **workspace.bindings.documents[document.id].model_dump(),
        )
        for document in corpus.documents
    )


def capture_utility_query(
    workspace: AuditWorkspace,
    corpus: UtilityCorpus,
    query: CorpusQuery,
    *,
    provider: GenerationProvider,
    store: QdrantChunkStore | None = None,
) -> QueryCapture:
    corpus = _corpus(corpus)
    query = CorpusQuery.model_validate(query.model_dump())
    if (
        query not in corpus.queries
        or workspace.template.pack_id != "utility-v1"
        or not workspace.bindings.seeded
        or not set(query.relevant_document_ids).issubset(workspace.bindings.documents)
    ):
        raise ValueError("utility_query_mismatch")
    workspace.validate_owned()
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
        raise ValueError("utility_store_mismatch")
    permitted = corpus.permitted_document_ids(query.actor_id)
    versions = tuple(
        workspace.bindings.documents[document.id].version_id
        for document in corpus.documents
        if document.id in permitted and document.id in workspace.bindings.documents
    )
    by_chunk = {
        chunk_id: binding
        for binding in workspace.bindings.documents.values()
        for chunk_id in binding.chunk_ids
    }
    observer = AuditObserver(
        case_id=query.id, canaries={}, known_chunk_ids=by_chunk.keys()
    )
    app = cast(FastAPI, workspace.client.app)
    previous = (
        getattr(app.state, "generation_provider", None),
        getattr(app.state, "observation_sink", None),
        getattr(app.state, "chunk_store", None),
    )
    error_code: ErrorCode | None = None
    response: AnswerResponse | None = None
    interrupted = False
    actor = next(actor for actor in corpus.actors if actor.id == query.actor_id)
    organization = next(
        org for org in corpus.organizations if org.id == actor.organization_id
    )
    (
        app.state.generation_provider,
        app.state.observation_sink,
        app.state.chunk_store,
    ) = provider, observer, selected
    try:
        headers = login_actor(
            workspace, email=actor.email, organization_slug=organization.slug
        )
        with workspace.mutation():
            result = workspace.client.post(
                "/api/v1/chat/query",
                headers=headers,
                json={"question": query.question, "limit": 20},
            )
        if result.status_code == 200:
            response = AnswerResponse.model_validate(result.json())
            observer.finish(200, response.status)
            if response.status == "failed":
                error_code = "runtime_failed"
        else:
            code = result.json().get("code")
            error_code = (
                cast(ErrorCode, code)
                if code in get_args(ErrorCode)
                else "runtime_failed"
            )
            observer.finish(result.status_code, "failed", error_code)
    except KeyboardInterrupt:
        interrupted = True
        error_code = "runtime_failed"
        observer.finish(503, "failed")
    except AuditWorkspaceError as error:
        error_code = (
            "audit_login_failed"
            if error.args == ("audit_login_failed",)
            else "runtime_failed"
        )
        observer.finish(401 if error_code == "audit_login_failed" else 503, "failed")
    except Exception:
        error_code = "runtime_failed"
        observer.finish(503, "failed")
    finally:
        (
            app.state.generation_provider,
            app.state.observation_sink,
            app.state.chunk_store,
        ) = previous
    observation = observer.snapshot()
    delivered = next(
        stage
        for stage in observation.boundaries
        if stage.boundary == Boundary.CITATIONS_DELIVERED
    )
    citations = tuple(
        CitationRecord(
            chunk_id=chunk_id,
            document_id=by_chunk[chunk_id].document_id,
            version_id=by_chunk[chunk_id].version_id,
        )
        for chunk_id in delivered.chunk_ids
    )
    if response is not None and (
        tuple(
            (item.chunk_id, item.document_id, item.version_id)
            for item in response.citations
        )
        != tuple(
            (item.chunk_id, item.document_id, item.version_id) for item in citations
        )
        or response.status == "answered"
        and (not response.answer or not citations)
    ):
        observation = observation.model_copy(
            update={"terminal": Terminal.OBSERVER_FAILED}
        )
    label: bool | None = None
    if (
        observation.terminal in {Terminal.ANSWERED, Terminal.ABSTAINED}
        and response is not None
    ):
        label = answer_label_matches(response.answer, query.expected_answer)
    elif error_code is None:
        error_code = "runtime_failed"
    return QueryCapture(
        UtilityQueryRecord(
            query_id=query.id,
            actor_id=query.actor_id,
            relevant_document_ids=query.relevant_document_ids,
            permitted_version_ids=versions,
            observation=observation,
            citations=citations,
            answer_label_match=label,
            error_code=error_code,
        ),
        interrupted,
    )


def execute_query_cohort(
    corpus: UtilityCorpus,
    capture: Callable[[CorpusQuery], QueryCapture],
    *,
    on_record: Callable[[tuple[UtilityQueryRecord, ...]], None] | None = None,
) -> UtilityExecution:
    corpus = _corpus(corpus)
    records: list[UtilityQueryRecord] = []
    runtime_failed = interrupted = False
    for query in corpus.queries:
        try:
            current = capture(query)
            checked = UtilityQueryRecord.model_validate(current.record.model_dump())
            if checked.query_id != query.id:
                raise ValueError("utility_query_mismatch")
            records.append(checked)
            runtime_failed |= checked.observation.terminal not in {
                Terminal.ANSWERED,
                Terminal.ABSTAINED,
            }
            interrupted |= current.interrupted
            if on_record is not None:
                on_record(tuple(records))
            if interrupted:
                runtime_failed = True
                break
        except KeyboardInterrupt:
            runtime_failed = interrupted = True
            break
        except Exception:
            runtime_failed = True
            break
    return UtilityExecution(tuple(records), runtime_failed, interrupted)


def execute_utility_queries(
    workspace: AuditWorkspace,
    corpus: UtilityCorpus,
    generation: GenerationRecord,
    *,
    strategy: IsolationStrategy = "shared_pre_filter",
    lab: bool = False,
    on_record: Callable[[tuple[UtilityQueryRecord, ...]], None] | None = None,
) -> UtilityExecution:
    corpus = _corpus(corpus)
    generation = GenerationRecord.model_validate(generation.model_dump())
    if workspace.template.checksum != utility_template(corpus).checksum:
        raise ValueError("utility_fixture_required")
    if (
        workspace.config.embedding_fingerprint != load_embedding_pins().fingerprint
        or workspace.config.embedding_dimension != 384
        or getattr(workspace.embeddings, "fingerprint", None)
        != workspace.config.embedding_fingerprint
        or workspace.embeddings.dimension != 384
        or cast(FastAPI, workspace.client.app).state.embeddings
        is not workspace.embeddings
    ):
        raise ValueError("utility_embedding_required")
    expected = (
        "tenant_collections"
        if strategy == "tenant_collections"
        else "shared_pre_filter"
    )
    if (
        strategy not in {"shared_pre_filter", "tenant_collections", "lab_post_filter"}
        or workspace.config.vector_strategy != expected
        or lab != (strategy == "lab_post_filter")
    ):
        raise ValueError("utility_strategy_mismatch")
    workspace.validate_owned()
    utility_document_bindings(workspace, corpus)
    provider: GenerationProvider = FixtureCitingProvider()
    if generation.local is not None:
        provider = generation.local.provider(workspace.settings)
    store = LabPostFilterStore(workspace, lab=True) if lab else workspace.store
    return execute_query_cohort(
        corpus,
        lambda query: capture_utility_query(
            workspace, corpus, query, provider=provider, store=store
        ),
        on_record=on_record,
    )
