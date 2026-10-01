from dataclasses import dataclass
from time import perf_counter
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chat.context import build_context
from app.chat.contracts import GenerationProvider
from app.chat.models import QueryRun, QueryTraceStage
from app.chat.schemas import (
    AnswerResponse,
    Citation,
    QueryCommand,
    StageResponse,
    TraceResponse,
)
from app.documents.extraction import DocumentError
from app.retrieval.service import AuthorizedRetriever
from app.tenancy.scope import AccessScope


@dataclass(frozen=True, slots=True)
class ChatOutcome:
    response: AnswerResponse
    error_code: str | None = None
    error_status: int = 200


def _stage(
    session: Session,
    run: QueryRun,
    stage: str,
    decision: str,
    started: float,
    ids: tuple[UUID, ...] = (),
) -> None:
    session.add(
        QueryTraceStage(
            organization_id=run.organization_id,
            user_id=run.user_id,
            query_run_id=run.id,
            stage=stage,
            decision=decision,
            duration_ms=(perf_counter() - started) * 1000,
            chunk_ids=[str(value) for value in ids],
        )
    )


def _failed(session: Session, run: QueryRun, code: str, status: int) -> ChatOutcome:
    run.state, run.error_code = "failed", code
    session.commit()
    return ChatOutcome(
        AnswerResponse(query_run_id=run.id, status="failed", answer=None, citations=[]),
        code,
        status,
    )


def query_documents(
    scope: AccessScope,
    command: QueryCommand,
    *,
    session: Session,
    retriever: AuthorizedRetriever,
    provider: GenerationProvider,
) -> ChatOutcome:
    run = QueryRun(organization_id=scope.organization_id, user_id=scope.user_id)
    session.add(run)
    session.flush()
    started = perf_counter()
    try:
        chunks = retriever.search(scope, command.question, command.limit)
    except Exception as error:
        code, status = (
            (error.code, error.status)
            if isinstance(error, DocumentError)
            else ("retrieval_unavailable", 503)
        )
        _stage(session, run, "retrieval", code, started)
        return _failed(session, run, code, status)
    _stage(
        session,
        run,
        "retrieval",
        "evidence_found" if chunks else "no_evidence",
        started,
        tuple(chunk.id for chunk in chunks),
    )
    started = perf_counter()
    context = build_context(chunks)
    _stage(
        session,
        run,
        "context",
        "bounded" if context else "empty",
        started,
        tuple(chunk.id for chunk in context),
    )
    if not context:
        run.state = "abstained"
        session.commit()
        return ChatOutcome(
            AnswerResponse(
                query_run_id=run.id, status="abstained", answer=None, citations=[]
            )
        )
    started = perf_counter()
    try:
        generation = provider.generate(command.question, context)
    except Exception as error:
        if isinstance(error, TimeoutError):
            code, status = "generation_timeout", 504
        elif isinstance(error, DocumentError):
            code, status = error.code, error.status
        else:
            code, status = "generation_unavailable", 502
        _stage(session, run, "generation", code, started)
        return _failed(session, run, code, status)
    _stage(session, run, "generation", "complete", started)
    started = perf_counter()
    manifest = {chunk.id: chunk for chunk in context}
    if any(identifier not in manifest for identifier in generation.citation_ids) or (
        generation.answer.strip() and not generation.citation_ids
    ):
        _stage(session, run, "citations", "invalid_citations", started)
        return _failed(session, run, "invalid_citations", 502)
    cited = tuple(dict.fromkeys(generation.citation_ids))
    _stage(session, run, "citations", "validated", started, cited)
    citations = [
        Citation(
            chunk_id=identifier,
            document_id=manifest[identifier].document_id,
            version_id=manifest[identifier].version_id,
            filename=manifest[identifier].filename,
            location=manifest[identifier].location,
        )
        for identifier in cited
    ]
    answer = generation.answer.strip() or None
    run.state = "answered" if answer else "abstained"
    session.commit()
    return ChatOutcome(
        AnswerResponse(
            query_run_id=run.id,
            status="answered" if answer else "abstained",
            answer=answer,
            citations=citations,
        )
    )


def query_trace(scope: AccessScope, run_id: UUID, *, session: Session) -> TraceResponse:
    run = session.scalar(
        select(QueryRun).where(
            QueryRun.id == run_id,
            QueryRun.organization_id == scope.organization_id,
            QueryRun.user_id == scope.user_id,
        )
    )
    if run is None:
        raise DocumentError("resource_not_found", 404)
    stages = session.scalars(
        select(QueryTraceStage)
        .where(QueryTraceStage.query_run_id == run.id)
        .order_by(QueryTraceStage.created_at, QueryTraceStage.id)
    )
    return TraceResponse(
        id=run.id,
        state=run.state,
        error_code=run.error_code,
        created_at=run.created_at,
        stages=[StageResponse.model_validate(stage) for stage in stages],
    )
