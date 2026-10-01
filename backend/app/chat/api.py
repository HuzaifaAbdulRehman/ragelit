from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.api.deps import CurrentAccessScope, DatabaseSession
from app.chat.deps import Embeddings, Generator
from app.chat.schemas import AnswerResponse, QueryCommand, TraceResponse
from app.chat.service import query_documents, query_trace
from app.core.problems import problem_response
from app.retrieval.deps import ChunkStore
from app.retrieval.service import AuthorizedRetriever

router = APIRouter(tags=["chat"])


@router.post(
    "/chat/query",
    response_model=AnswerResponse,
    responses={
        status: {
            "description": "Query dependency failure",
            "content": {
                "application/problem+json": {
                    "schema": {"$ref": "#/components/schemas/ProblemDetail"}
                }
            },
        }
        for status in (502, 503, 504)
    },
)
def chat_query(
    command: QueryCommand,
    scope: CurrentAccessScope,
    session: DatabaseSession,
    store: ChunkStore,
    embeddings: Embeddings,
    provider: Generator,
) -> JSONResponse:
    outcome = query_documents(
        scope,
        command,
        session=session,
        retriever=AuthorizedRetriever(session, store, embeddings),
        provider=provider,
    )
    if outcome.error_code:
        return problem_response(
            status=outcome.error_status,
            code=outcome.error_code,
            title="Query could not be completed",
            detail="The query could not be completed. Its trace is available.",
            extensions={"query_run_id": str(outcome.response.query_run_id)},
        )
    return JSONResponse(outcome.response.model_dump(mode="json"))


@router.get("/query-runs/{run_id}", response_model=TraceResponse)
def trace(
    run_id: UUID, scope: CurrentAccessScope, session: DatabaseSession
) -> TraceResponse:
    return query_trace(scope, run_id, session=session)
