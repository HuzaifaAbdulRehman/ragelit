from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient, models

from app.audits.contracts import Boundary, Terminal
from app.audits.injection_fixtures import fact_marker, generate_injection_fixtures
from app.audits.injection_providers import InjectionProvider
from app.audits.injection_reports import expected_injection_cases
from app.audits.observer import AuditObserver
from app.audits.target import PreparedPack, PreparedRequest
from app.audits.workspace import AuditConfiguration, AuditWorkspace, FixtureBindings
from app.evaluation.models import load_embedding_pins
from app.evaluation.reports import GenerationRecord
from app.retrieval.contracts import AuthorizedChunk
from app.retrieval.store import QdrantChunkStore
from app.tenancy.enums import Role
from app.tenancy.scope import AccessScope
from tests.unit.audits.test_injection_reports import bindings, report
from tests.unit.audits.test_workspace import configuration


@pytest.fixture
def injection_workspace(tmp_path: Path) -> Iterator[SimpleNamespace]:
    template = generate_injection_fixtures()
    controls = expected_injection_cases(bindings(), trials=1)
    app = FastAPI()
    config = AuditConfiguration.model_validate(
        configuration(tmp_path).model_dump()
        | {
            "embedding_fingerprint": load_embedding_pins().fingerprint,
            "embedding_dimension": 384,
        }
    )
    embeddings = SimpleNamespace(
        fingerprint=config.embedding_fingerprint, dimension=384
    )
    app.state.embeddings = embeddings
    qdrant = QdrantClient(":memory:")
    store = QdrantChunkStore(qdrant, config.name, dimension=384)
    app.state.generation_provider = object()
    app.state.observation_sink = object()
    app.state.chunk_store = object()
    prior = (
        app.state.generation_provider,
        app.state.observation_sink,
        app.state.chunk_store,
    )
    events: list[str] = []
    mode = ["answer"]

    @app.post("/api/v1/auth/login")
    async def login(request: Request) -> JSONResponse:
        body = await request.json()
        assert body["email"] in {actor.email for actor in template.actors}
        events.append("login")
        if mode[0] == "login_failed":
            return JSONResponse({"code": "authentication_failed"}, status_code=401)
        return JSONResponse({"access_token": f"test-session-{len(events)}"})

    @app.post("/api/v1/chat/query")
    async def query(request: Request) -> JSONResponse:
        assert request.headers["authorization"] == f"Bearer test-session-{len(events)}"
        events.append("query")
        observer = cast(AuditObserver, app.state.observation_sink)
        observer.scope(
            AccessScope(UUID(int=1), UUID(int=2), UUID(int=3), Role.MEMBER, ())
        )
        body = await request.json()
        document = next(
            doc for doc in template.documents if doc.question == body["question"]
        )
        binding = bindings()[document.id]
        chunk = AuthorizedChunk(
            id=binding.chunk_ids[0],
            document_id=binding.document_id,
            version_id=binding.version_id,
            filename="fixture.txt",
            text=document.text,
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
        if mode[0] == "explode":
            raise RuntimeError("ExceptionSecretMarker")
        if mode[0] == "timeout":
            observer.finish(504, "failed", "generation_timeout")
            return JSONResponse({"code": "generation_timeout"}, status_code=504)
        generated = app.state.generation_provider.generate(document.question, (chunk,))
        observer.generation(generated.answer, generated.citation_ids, 3.0)
        observer.delivery(generated.answer, generated.citation_ids)
        observer.finish(200, "answered")
        return JSONResponse({"status": "answered"})

    @contextmanager
    def mutation() -> Iterator[None]:
        yield

    pack = PreparedPack(
        uuid4(),
        tuple(control.access_case for control in controls),
        MappingProxyType(
            {
                logical.id: PreparedRequest(
                    next(
                        doc.question
                        for doc in template.documents
                        if doc.id == logical.document_id
                    ),
                    MappingProxyType({"Authorization": "Bearer stale-test-session"}),
                )
                for logical in template.cases
            }
        ),
        MappingProxyType(
            template.canaries
            | dict(fact_marker(template.seed, doc.id) for doc in template.documents)
        ),
        frozenset(chunk for item in bindings().values() for chunk in item.chunk_ids),
    )
    with TestClient(app) as client:
        owned = cast(
            AuditWorkspace,
            SimpleNamespace(
                config=config,
                embeddings=embeddings,
                template=template,
                store=store,
                client=client,
                bindings=FixtureBindings(
                    workspace_id=config.workspace_id,
                    template_hash=template.checksum,
                    collection=config.name,
                    seeded=True,
                    documents=bindings(),
                ),
                validate_owned=lambda: None,
                mutation=mutation,
            ),
        )
        yield SimpleNamespace(
            workspace=owned,
            pack=pack,
            controls=controls,
            app=app,
            prior=prior,
            events=events,
            mode=mode,
        )
    qdrant.close()


def test_security_query_refreshes_auth_and_restores_app_state(
    injection_workspace: SimpleNamespace,
) -> None:
    from app.evaluation.security_runner import capture_injection_query

    fixture = injection_workspace
    for _ in range(2):
        captured = capture_injection_query(
            fixture.workspace,
            fixture.pack,
            fixture.controls[0],
            provider=InjectionProvider("resistant"),
        )
        assert captured.observation.terminal == Terminal.ANSWERED
        assert captured.observation.scope_hash is not None
        assert not captured.runtime_failed
        assert not captured.interrupted
        assert (
            fixture.app.state.generation_provider,
            fixture.app.state.observation_sink,
            fixture.app.state.chunk_store,
        ) == fixture.prior
        content = captured.observation.model_dump_json()
        assert all(
            marker not in content
            for marker in (
                "Bearer ",
                "test-session-",
                "ExceptionSecretMarker",
                '"question":',
                '"answer":',
                '"text":',
            )
        )
    assert fixture.events == ["login", "query", "login", "query"]


@pytest.mark.parametrize(
    "mode,terminal",
    [
        ("timeout", Terminal.RUNTIME_FAILED),
        ("explode", Terminal.RUNTIME_FAILED),
        ("login_failed", Terminal.AUTHENTICATION_DENIED),
    ],
)
def test_security_runtime_failure_retains_observed_stages(
    injection_workspace: SimpleNamespace,
    mode: str,
    terminal: Terminal,
) -> None:
    from app.evaluation.security_runner import capture_injection_query

    fixture = injection_workspace
    fixture.mode[0] = mode
    captured = capture_injection_query(
        fixture.workspace,
        fixture.pack,
        fixture.controls[0],
        provider=InjectionProvider("resistant"),
    )
    assert captured.observation.terminal == terminal
    assert captured.runtime_failed
    assert not captured.interrupted
    stages = {stage.boundary: stage for stage in captured.observation.boundaries}
    assert stages[Boundary.CONTEXT].state == (
        "not_reached" if mode == "login_failed" else "observed"
    )
    assert stages[Boundary.OUTPUT_DELIVERED].state == (
        "not_reached" if mode == "login_failed" else "unobserved"
    )
    assert (
        fixture.app.state.generation_provider,
        fixture.app.state.observation_sink,
        fixture.app.state.chunk_store,
    ) == fixture.prior
    assert "ExceptionSecretMarker" not in captured.observation.model_dump_json()


def test_injection_cohort_continues_failures_and_preserves_inflight_interrupt() -> None:
    from app.evaluation.security_runner import (
        InjectionCapture,
        execute_injection_cohort,
    )

    controls = expected_injection_cases(bindings(), trials=1)
    raw = tuple(item.access_control.observation for item in report().results)
    snapshots: list[Any] = []

    def capture(control: Any) -> Any:
        index = next(i for i, case in enumerate(controls) if case == control)
        observed = raw[index]
        if index == 0:
            observed = observed.model_copy(
                update={"http_status": 503, "terminal": Terminal.RUNTIME_FAILED}
            )
        return InjectionCapture(observed, index in {0, 2}, index == 2)

    result = execute_injection_cohort(controls, capture, on_record=snapshots.append)
    assert tuple(item.case_id for item in result.observations) == (
        "org-1:benign:trial-1",
        "org-1:attack:trial-1",
        "org-2:benign:trial-1",
    )
    assert result.runtime_failed
    assert result.interrupted
    assert [len(item.observations) for item in snapshots] == [1, 2, 3]
    assert all(item.runtime_failed for item in snapshots)


def test_checkpoint_failure_retains_latest_record_without_retry() -> None:
    from app.evaluation.security_runner import (
        InjectionCapture,
        execute_injection_cohort,
    )

    controls = expected_injection_cases(bindings(), trials=1)
    raw = report().results[0].access_control.observation

    def fail(snapshot: Any) -> None:
        raise OSError("ExceptionSecretMarker")

    result = execute_injection_cohort(
        controls, lambda case: InjectionCapture(raw), on_record=fail
    )
    assert len(result.observations) == 1
    assert result.observations[0].case_id == "org-1:benign:trial-1"
    assert result.runtime_failed
    assert not result.interrupted


def test_full_injection_entry_uses_declared_profile_and_complete_logical_cohort(
    injection_workspace: SimpleNamespace,
) -> None:
    from app.evaluation.security_reports import build_injection_benchmark
    from app.evaluation.security_runner import execute_injection_benchmark
    from tests.unit.evaluation.report_support import provenance

    fixture = injection_workspace
    result = execute_injection_benchmark(
        fixture.workspace,
        fixture.pack,
        GenerationRecord(mode="fixture"),
        strategy="shared_pre_filter",
        provider_profile="obeying",
    )
    assert tuple(item.case_id for item in result.observations) == (
        "org-1:benign:trial-1",
        "org-1:attack:trial-1",
        "org-2:benign:trial-1",
        "org-2:attack:trial-1",
        "org-3:benign:trial-1",
        "org-3:attack:trial-1",
    )
    assert not result.runtime_failed
    assert not result.interrupted
    measured = build_injection_benchmark(
        provenance(),
        bindings(),
        result.observations,
        strategy="shared_pre_filter",
        collection_names=("ragelit_audit_test",),
        provider_profile="obeying",
    )
    assert measured.injection.primary_rate == 1.0
    assert measured.exit_code == 1
    assert fixture.events == ["login", "query"] * 6


@pytest.mark.parametrize("drift", ["registry", "known_chunks", "embedding", "lab"])
def test_full_injection_entry_rejects_unbound_evidence_before_authentication(
    injection_workspace: SimpleNamespace,
    drift: str,
) -> None:
    from app.evaluation.security_runner import execute_injection_benchmark

    fixture = injection_workspace
    pack = fixture.pack
    if drift == "registry":
        pack = replace(pack, canaries=MappingProxyType({}))
    elif drift == "known_chunks":
        pack = replace(pack, known_chunk_ids=frozenset())
    elif drift == "embedding":
        fixture.app.state.embeddings = object()
    with pytest.raises(ValueError, match="injection_benchmark_"):
        execute_injection_benchmark(
            fixture.workspace,
            pack,
            GenerationRecord(mode="fixture"),
            strategy="shared_pre_filter",
            lab=drift == "lab",
        )
    assert fixture.events == []
