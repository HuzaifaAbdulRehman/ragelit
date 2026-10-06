from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient, models

from app.audits.contracts import Boundary, Terminal
from app.audits.observer import AuditObserver
from app.audits.seeding import FixtureCitingProvider
from app.audits.workspace import AuditWorkspace, DocumentBinding, FixtureBindings
from app.evaluation.dataset import CorpusQuery, generate_utility_corpus
from app.evaluation.workspace import utility_template
from app.retrieval.contracts import AuthorizedChunk
from app.retrieval.store import QdrantChunkStore
from app.tenancy.enums import Role
from app.tenancy.scope import AccessScope
from tests.unit.audits.test_workspace import configuration
from tests.unit.evaluation.report_support import bindings, record


@pytest.fixture
def capture_workspace(tmp_path: Path) -> Iterator[SimpleNamespace]:
    corpus = generate_utility_corpus()
    app = FastAPI()
    bound = bindings()
    config = configuration(tmp_path)
    client = QdrantClient(":memory:")
    store = QdrantChunkStore(client, config.name, dimension=384)
    app.state.generation_provider = object()
    app.state.observation_sink = object()
    app.state.chunk_store = object()
    initial = (
        app.state.generation_provider,
        app.state.observation_sink,
        app.state.chunk_store,
    )
    events: list[dict[str, Any]] = []
    mode = ["answer"]

    @app.post("/api/v1/auth/login")
    async def login(request: Request) -> JSONResponse:
        body = await request.json()
        events.append(
            {"kind": "login", "email": body["email"], "slug": body["organization_slug"]}
        )
        if mode[0] == "login_failed":
            return JSONResponse({"code": "authentication_failed"}, status_code=401)
        assert body["password"] == config.fixture_password.get_secret_value()
        return JSONResponse({"access_token": f"synthetic-{len(events)}"})

    @app.post("/api/v1/chat/query")
    async def chat(request: Request) -> JSONResponse:
        assert request.headers["authorization"] == f"Bearer synthetic-{len(events)}"
        body = await request.json()
        events.append(
            {"kind": "query", "question": body["question"], "limit": body["limit"]}
        )
        if mode[0] == "explode":
            raise RuntimeError("ExceptionSecretMarker")
        observer = cast(AuditObserver, app.state.observation_sink)
        observer.scope(
            AccessScope(
                UUID(int=11),
                UUID(int=12),
                UUID(int=13),
                Role.MEMBER,
                (UUID(int=14),),
            )
        )
        item = bound[0]
        chunk = AuthorizedChunk(
            id=item.chunk_ids[0],
            document_id=item.document_id,
            version_id=item.version_id,
            filename="policy.txt",
            text=corpus.documents[0].text,
            location="paragraph 1",
            score=1.0,
        )
        observer.retrieval(
            (
                models.ScoredPoint(
                    id=str(chunk.id),
                    version=1,
                    score=1.0,
                    payload={"chunk_id": str(chunk.id), "text": chunk.text},
                ),
            ),
            1.0,
        )
        observer.chunks("retrieval_accepted", (chunk,), 2.0)
        observer.chunks("context", (chunk,), 0.5)
        if mode[0] == "timeout":
            observer.finish(504, "failed", "generation_timeout")
            return JSONResponse({"code": "generation_timeout"}, status_code=504)
        answer = corpus.queries[0].expected_answer
        observer.generation(answer, (chunk.id,), 3.0)
        observer.delivery(answer, (chunk.id,))
        observer.finish(200, "answered")
        return JSONResponse(
            {
                "query_run_id": str(UUID(int=99)),
                "status": "answered",
                "answer": answer,
                "citations": [
                    {
                        "chunk_id": str(chunk.id),
                        "document_id": str(chunk.document_id),
                        "version_id": str(chunk.version_id),
                        "filename": chunk.filename,
                        "location": chunk.location,
                    }
                ],
            }
        )

    @contextmanager
    def mutation() -> Iterator[None]:
        yield

    with TestClient(app) as transport:
        workspace = SimpleNamespace(
            config=config,
            store=store,
            client=transport,
            template=utility_template(corpus),
            bindings=FixtureBindings(
                workspace_id=config.workspace_id,
                template_hash=utility_template(corpus).checksum,
                collection=config.name,
                seeded=True,
                documents={
                    item.logical_id: DocumentBinding(
                        document_id=item.document_id,
                        version_id=item.version_id,
                        chunk_ids=item.chunk_ids,
                        content_hash=item.content_hash,
                    )
                    for item in bound
                },
            ),
            validate_owned=lambda: None,
            mutation=mutation,
        )
        yield SimpleNamespace(
            workspace=cast(AuditWorkspace, workspace),
            corpus=corpus,
            app=app,
            initial=initial,
            events=events,
            mode=mode,
        )
    client.close()


@pytest.mark.parametrize(
    ("answer", "expected", "match"),
    [
        ("It is 80 hours.", "80 hours", True),
        ("It is 80   HOURS.", "80 hours", True),
        ("It is 180 hours.", "80 hours", False),
        ("It is 1.80 hours.", "80 hours", False),
        ("It is 80 hours-long.", "80 hours", True),
        ("It is 80 hoursx.", "80 hours", False),
        ("", "80 hours", False),
        (None, "80 hours", False),
    ],
)
def test_fact_match_is_normalized_but_not_a_numeric_substring(
    answer: str | None,
    expected: str,
    match: bool,
) -> None:
    from app.evaluation.runner import answer_label_matches

    assert answer_label_matches(answer, expected) is match


def test_each_query_logs_in_again_and_restores_application_state(
    capture_workspace: SimpleNamespace,
) -> None:
    from app.evaluation.runner import capture_utility_query

    fixture = capture_workspace
    query = fixture.corpus.queries[0]
    captures = tuple(
        capture_utility_query(
            fixture.workspace,
            fixture.corpus,
            query,
            provider=FixtureCitingProvider(),
        )
        for _ in range(2)
    )
    assert [event["kind"] for event in fixture.events] == [
        "login",
        "query",
        "login",
        "query",
    ]
    assert all(
        event["limit"] == 20 for event in fixture.events if event["kind"] == "query"
    )
    for capture in captures:
        assert capture.record.query_id == "org-1:change-notice"
        assert capture.record.answer_label_match is True
        assert capture.record.observation.terminal == Terminal.ANSWERED
        assert capture.record.observation.scope_hash is not None
        assert capture.record.citations[0].document_id == UUID(int=100)
        assert not capture.interrupted
    assert (
        fixture.app.state.generation_provider,
        fixture.app.state.observation_sink,
        fixture.app.state.chunk_store,
    ) == fixture.initial
    content = captures[0].record.model_dump_json()
    assert "synthetic-" not in content
    assert "Bearer " not in content
    assert '"answer":' not in content
    assert '"question":' not in content
    assert '"text":' not in content


@pytest.mark.parametrize(
    ("mode", "terminal", "code"),
    [
        ("timeout", Terminal.RUNTIME_FAILED, "generation_timeout"),
        ("login_failed", Terminal.AUTHENTICATION_DENIED, "audit_login_failed"),
        ("explode", Terminal.RUNTIME_FAILED, "runtime_failed"),
    ],
)
def test_runtime_and_login_failures_are_preserved_without_exception_text(
    capture_workspace: SimpleNamespace,
    mode: str,
    terminal: Terminal,
    code: str,
) -> None:
    from app.evaluation.runner import capture_utility_query

    fixture = capture_workspace
    fixture.mode[0] = mode
    captured = capture_utility_query(
        fixture.workspace,
        fixture.corpus,
        fixture.corpus.queries[0],
        provider=FixtureCitingProvider(),
    )
    assert captured.record.observation.terminal == terminal
    assert captured.record.error_code == code
    assert captured.record.answer_label_match is None
    assert not captured.interrupted
    assert "ExceptionSecretMarker" not in captured.record.model_dump_json()
    assert fixture.app.state.generation_provider is fixture.initial[0]
    assert fixture.app.state.observation_sink is fixture.initial[1]
    assert fixture.app.state.chunk_store is fixture.initial[2]


def test_cohort_retains_an_ordinary_failure_and_does_not_drop_later_queries() -> None:
    from app.evaluation.runner import QueryCapture, execute_query_cohort

    corpus = generate_utility_corpus()
    calls: list[str] = []

    def capture(query: CorpusQuery) -> QueryCapture:
        index = len(calls)
        calls.append(query.id)
        item = record(index)
        if index == 0:
            stages = tuple(
                stage.model_copy(update={"state": "unobserved", "chunk_ids": ()})
                if stage.boundary
                not in {
                    Boundary.RETRIEVAL_RAW,
                    Boundary.RETRIEVAL_ACCEPTED,
                    Boundary.CONTEXT,
                }
                else stage
                for stage in item.observation.boundaries
            )
            item = item.model_copy(
                update={
                    "observation": item.observation.model_copy(
                        update={
                            "http_status": 504,
                            "terminal": Terminal.RUNTIME_FAILED,
                            "boundaries": stages,
                        }
                    ),
                    "citations": (),
                    "answer_label_match": None,
                    "error_code": "generation_timeout",
                }
            )
        return QueryCapture(item, False)

    result = execute_query_cohort(corpus, capture)
    assert len(result.records) == 87
    assert calls[0] == "org-1:change-notice"
    assert calls[-1] == "org-3:meal-allowance"
    assert result.runtime_failed
    assert result.records[0].error_code == "generation_timeout"
    assert result.records[-1].answer_label_match is True
    assert not result.interrupted


def test_cohort_retains_inflight_interruption_and_stops() -> None:
    from app.evaluation.runner import QueryCapture, execute_query_cohort

    calls = 0

    def capture(query: CorpusQuery) -> QueryCapture:
        nonlocal calls
        current = calls
        calls += 1
        item = record(current)
        return QueryCapture(item, current == 1)

    result = execute_query_cohort(generate_utility_corpus(), capture)
    assert len(result.records) == 2
    assert calls == 2
    assert result.runtime_failed
    assert result.interrupted


def test_checkpoint_failure_keeps_completed_records_and_stops_without_retry() -> None:
    from app.evaluation.runner import QueryCapture, execute_query_cohort

    calls: list[str] = []

    def capture(query: CorpusQuery) -> QueryCapture:
        index = len(calls)
        calls.append(query.id)
        return QueryCapture(record(index), False)

    def checkpoint(items: tuple[Any, ...]) -> None:
        raise OSError("ExceptionSecretMarker")

    result = execute_query_cohort(
        generate_utility_corpus(), capture, on_record=checkpoint
    )
    assert len(result.records) == 1
    assert len(calls) == 1
    assert result.runtime_failed
    assert not result.interrupted


def test_capture_interrupt_returns_redacted_partial_evidence_and_restores_state(
    capture_workspace: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.evaluation.runner import capture_utility_query

    fixture = capture_workspace
    original = fixture.workspace.client.post

    def interrupted(url: str, **kwargs: Any) -> Any:
        if url == "/api/v1/chat/query":
            raise KeyboardInterrupt
        return original(url, **kwargs)

    monkeypatch.setattr(fixture.workspace.client, "post", interrupted)
    captured = capture_utility_query(
        fixture.workspace,
        fixture.corpus,
        fixture.corpus.queries[0],
        provider=FixtureCitingProvider(),
    )
    assert captured.interrupted
    assert captured.record.error_code == "runtime_failed"
    assert captured.record.observation.terminal == Terminal.RUNTIME_FAILED
    assert all(
        stage.state == "unobserved" for stage in captured.record.observation.boundaries
    )
    assert fixture.app.state.observation_sink is fixture.initial[1]


def test_unknown_query_is_rejected_before_login(
    capture_workspace: SimpleNamespace,
) -> None:
    from app.evaluation.runner import capture_utility_query

    fixture = capture_workspace
    query = fixture.corpus.queries[0].model_copy(update={"actor_id": "org-2-owner"})
    with pytest.raises(ValueError):
        capture_utility_query(
            fixture.workspace, fixture.corpus, query, provider=FixtureCitingProvider()
        )
    assert fixture.events == []


def test_changed_corpus_is_rejected_before_any_capture() -> None:
    from app.evaluation.runner import execute_query_cohort

    def unreachable(query: CorpusQuery) -> Any:
        pytest.fail("changed corpus reached runtime")

    with pytest.raises(ValueError, match="utility_corpus_drift"):
        execute_query_cohort(generate_utility_corpus(20261006), unreachable)


def test_full_benchmark_rejects_unbound_embedding_before_query(
    capture_workspace: SimpleNamespace,
) -> None:
    from app.evaluation.reports import GenerationRecord
    from app.evaluation.runner import execute_utility_queries

    fixture = capture_workspace
    with pytest.raises(ValueError, match="utility_embedding_required"):
        execute_utility_queries(
            fixture.workspace, fixture.corpus, GenerationRecord(mode="fixture")
        )
    assert fixture.events == []
