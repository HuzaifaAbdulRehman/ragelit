from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient, models

from app.audits.contracts import Boundary, Terminal
from app.audits.fixtures import generate_fixtures
from app.audits.observer import AuditObserver
from app.audits.seeding import FixtureCitingProvider
from app.audits.target import (
    PreparedPack,
    PreparedRequest,
    _evidence_registry,
    _instance_text,
)
from app.audits.workspace import AuditConfiguration, AuditWorkspace
from app.chat.contracts import Generation
from app.evaluation.access_registry import access_registry
from app.evaluation.models import load_embedding_pins
from app.evaluation.reports import GenerationRecord
from app.retrieval.contracts import AuthorizedChunk
from app.retrieval.store import QdrantChunkStore
from app.tenancy.enums import Role
from app.tenancy.scope import AccessScope
from tests.unit.audits.test_workspace import configuration
from tests.unit.evaluation.test_access_registry import RUN_ID, access_inventory


@pytest.fixture
def access_workspace(tmp_path: Path) -> Iterator[SimpleNamespace]:
    template = generate_fixtures()
    config = AuditConfiguration.model_validate(
        configuration(tmp_path).model_dump()
        | {
            "embedding_fingerprint": load_embedding_pins().fingerprint,
            "embedding_dimension": 384,
        }
    )
    inventory = access_inventory().model_copy(
        update={"collection": config.name, "workspace_id": config.workspace_id}
    )
    cases = access_registry(inventory, RUN_ID).cases
    app = FastAPI()
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
    emails: list[str] = []
    mode = ["normal"]

    @app.post("/api/v1/auth/login")
    async def login(request: Request) -> JSONResponse:
        body = await request.json()
        emails.append(body["email"])
        events.append("login")
        if mode[0] == "login_failed":
            return JSONResponse({"code": "authentication_failed"}, status_code=401)
        return JSONResponse({"access_token": f"test-session-{len(events)}"})

    @app.post("/api/v1/chat/query")
    async def query(request: Request) -> JSONResponse:
        observer = cast(AuditObserver, app.state.observation_sink)
        case = next(item for item in cases if item.id == observer.snapshot().case_id)
        if case.expected_status == 401:
            assert (
                request.headers["authorization"] == "Bearer pre-revocation-test-session"
            )
            events.append("query")
            return JSONResponse(
                {
                    "code": "authentication_failed"
                    if mode[0] == "expired"
                    else "membership_inactive"
                },
                status_code=401,
            )
        assert request.headers["authorization"] == f"Bearer test-session-{len(events)}"
        events.append("query")
        if case.expected_status == 422:
            body = await request.json()
            assert "organization_id" in body and "role" in body
            return JSONResponse({"code": "invalid_input"}, status_code=422)
        observer.scope(
            AccessScope(UUID(int=1), UUID(int=2), UUID(int=3), Role.MEMBER, ())
        )
        logical = next(item for item in template.cases if item.id == case.id)
        document = next(
            item for item in template.documents if item.id == logical.document_id
        )
        chunks: tuple[AuthorizedChunk, ...] = ()
        if case.positive or case.citation_challenge is not None:
            key = f"{RUN_ID}:{logical.id}"
            if case.citation_challenge is not None:
                document = next(
                    item
                    for item in template.documents
                    if item.id == f"{document.organization_id}-anchor"
                )
                binding = inventory.documents[document.id]
                text = document.text
            elif key in inventory.instances:
                instance = inventory.instances[key]
                assert instance.document is not None
                binding = instance.document
                text = _instance_text(document, key, replacement=True)
            else:
                binding, text = inventory.documents[document.id], document.text
            chunks = (
                AuthorizedChunk(
                    id=binding.chunk_ids[0],
                    document_id=binding.document_id,
                    version_id=binding.version_id,
                    filename="fixture.txt",
                    text=text,
                    location="paragraph 1",
                    score=1.0,
                ),
            )
        observer.retrieval(
            tuple(
                models.ScoredPoint(
                    id=str(chunk.id),
                    version=1,
                    score=1.0,
                    payload={"chunk_id": str(chunk.id), "text": chunk.text},
                )
                for chunk in chunks
            ),
            1.0,
        )
        observer.chunks("retrieval_accepted", chunks, 2.0)
        observer.chunks("context", chunks, 0.5)
        if mode[0] == "explode":
            raise RuntimeError("ExceptionSecretMarker")
        if mode[0] == "timeout":
            observer.finish(504, "failed", "generation_timeout")
            return JSONResponse({"code": "generation_timeout"}, status_code=504)
        if not chunks:
            observer.finish(200, "abstained")
            return JSONResponse({"status": "abstained"})
        generated = app.state.generation_provider.generate(document.question, chunks)
        observer.generation(generated.answer, generated.citation_ids, 3.0)
        if case.citation_challenge is not None:
            assert generated.citation_ids == (chunks[0].id, case.citation_challenge)
            observer.finish(502, "failed", "invalid_citations")
            return JSONResponse({"code": "invalid_citations"}, status_code=502)
        observer.delivery(generated.answer, generated.citation_ids)
        observer.finish(200, "answered")
        return JSONResponse({"status": "answered"})

    @contextmanager
    def mutation() -> Iterator[None]:
        yield

    with TestClient(app) as client:
        owned = cast(
            AuditWorkspace,
            SimpleNamespace(
                config=config,
                template=template,
                bindings=inventory,
                client=client,
                store=store,
                embeddings=embeddings,
                validate_owned=lambda: None,
                mutation=mutation,
            ),
        )
        evidence = _evidence_registry(owned)
        pack = PreparedPack(
            RUN_ID,
            cases,
            MappingProxyType(
                {
                    logical.id: PreparedRequest(
                        next(
                            doc.question
                            for doc in template.documents
                            if doc.id == logical.document_id
                        ),
                        MappingProxyType(
                            {"Authorization": "Bearer pre-revocation-test-session"}
                        ),
                        instance_key=f"{RUN_ID}:{logical.id}"
                        if f"{RUN_ID}:{logical.id}" in inventory.instances
                        else None,
                        citation_challenge=next(
                            case.citation_challenge
                            for case in cases
                            if case.id == logical.id
                        ),
                        forge_metadata=logical.action == "forge_metadata",
                    )
                    for logical in template.cases
                }
            ),
            MappingProxyType({item.canary_id: item.canary for item in evidence}),
            frozenset(chunk for item in evidence for chunk in item.binding.chunk_ids),
        )
        yield SimpleNamespace(
            workspace=owned,
            pack=pack,
            cases={case.id: case for case in cases},
            app=app,
            prior=prior,
            events=events,
            emails=emails,
            mode=mode,
        )
    qdrant.close()


class LocalStub:
    def __init__(self) -> None:
        self.calls = 0

    def generate(
        self, question: str, context: tuple[AuthorizedChunk, ...]
    ) -> Generation:
        self.calls += 1
        return Generation("local stub answer", (context[0].id,))


def test_access_capture_refreshes_normal_and_isolated_actor_logins(
    access_workspace: SimpleNamespace,
) -> None:
    from app.evaluation.access_runner import capture_access_query

    fixture = access_workspace
    provider = LocalStub()
    for identifier in ("org-1:organization", "org-1:version-new"):
        result = capture_access_query(
            fixture.workspace,
            fixture.pack,
            fixture.cases[identifier],
            provider=provider,
            provider_profile="local",
        )
        assert result.record.observation.terminal == Terminal.ANSWERED
        assert result.record.provider_profile == "local"
        assert not result.runtime_failed
        assert (
            fixture.app.state.generation_provider,
            fixture.app.state.observation_sink,
            fixture.app.state.chunk_store,
        ) == fixture.prior
    assert provider.calls == 2
    assert fixture.events == ["login", "query", "login", "query"]
    actor = fixture.workspace.bindings.instances[f"{RUN_ID}:org-1:version-new"].actor_id
    assert fixture.emails == [
        "org-1-engineering-member@example.invalid",
        f"instance-{actor.hex}@example.invalid",
    ]


@pytest.mark.parametrize("expired", [False, True])
def test_membership_denial_uses_pre_revocation_session_and_expiry_is_failure(
    access_workspace: SimpleNamespace, expired: bool
) -> None:
    from app.evaluation.access_runner import capture_access_query

    fixture = access_workspace
    fixture.mode[0] = "expired" if expired else "normal"
    result = capture_access_query(
        fixture.workspace,
        fixture.pack,
        fixture.cases["org-1:membership-revoked"],
        provider=FixtureCitingProvider(),
        provider_profile="fixture",
    )
    assert fixture.events == ["query"]
    assert result.record.observation.http_status == 401
    assert result.record.observation.denial_code == (
        "authentication_failed" if expired else "membership_inactive"
    )
    assert result.runtime_failed == expired


def test_controlled_citation_keeps_fixture_generation_and_rejection(
    access_workspace: SimpleNamespace,
) -> None:
    from app.evaluation.access_runner import capture_access_query

    fixture = access_workspace
    provider = LocalStub()
    result = capture_access_query(
        fixture.workspace,
        fixture.pack,
        fixture.cases["org-1:citation"],
        provider=provider,
        provider_profile="local",
    )
    assert provider.calls == 0
    assert result.record.provider_profile == "controlled_citation"
    assert result.record.observation.terminal == Terminal.CITATIONS_REJECTED
    assert not result.runtime_failed
    stages = {stage.boundary: stage for stage in result.record.observation.boundaries}
    assert (
        fixture.cases["org-1:citation"].citation_challenge
        in stages[Boundary.CITATIONS_CANDIDATE].chunk_ids
    )
    assert stages[Boundary.OUTPUT_DELIVERED].state == "not_reached"


@pytest.mark.parametrize("mode", ["explode", "timeout", "login_failed"])
def test_access_capture_retains_observed_stages_on_failure(
    access_workspace: SimpleNamespace, mode: str
) -> None:
    from app.evaluation.access_runner import capture_access_query

    fixture = access_workspace
    fixture.mode[0] = mode
    result = capture_access_query(
        fixture.workspace,
        fixture.pack,
        fixture.cases["org-1:organization"],
        provider=FixtureCitingProvider(),
        provider_profile="fixture",
    )
    assert result.runtime_failed
    stages = {stage.boundary: stage for stage in result.record.observation.boundaries}
    assert stages[Boundary.CONTEXT].state == (
        "not_reached" if mode == "login_failed" else "observed"
    )
    assert (
        fixture.app.state.generation_provider,
        fixture.app.state.observation_sink,
        fixture.app.state.chunk_store,
    ) == fixture.prior
    assert "ExceptionSecretMarker" not in result.record.model_dump_json()


@pytest.mark.parametrize("drift", ["canaries", "request", "embedding"])
def test_access_entry_rejects_unbound_pack_before_authentication(
    access_workspace: SimpleNamespace, drift: str
) -> None:
    from app.evaluation.access_runner import execute_access_benchmark

    fixture = access_workspace
    pack = fixture.pack
    if drift == "canaries":
        pack = replace(pack, canaries=MappingProxyType({}))
    elif drift == "request":
        requests = dict(pack.requests)
        requests["org-1:citation"] = replace(
            requests["org-1:citation"], citation_challenge=None
        )
        pack = replace(pack, requests=MappingProxyType(requests))
    else:
        fixture.app.state.embeddings = object()
    with pytest.raises(ValueError, match="access_benchmark_"):
        execute_access_benchmark(
            fixture.workspace,
            pack,
            GenerationRecord(mode="fixture"),
            strategy="shared_pre_filter",
        )
    assert fixture.events == []


def test_access_entry_checkpoints_the_complete_canonical_cohort(
    access_workspace: SimpleNamespace,
) -> None:
    from app.evaluation.access_reports import build_access_benchmark
    from app.evaluation.access_runner import execute_access_benchmark
    from tests.unit.evaluation.report_support import provenance

    fixture = access_workspace
    snapshots: list[Any] = []
    result = execute_access_benchmark(
        fixture.workspace,
        fixture.pack,
        GenerationRecord(mode="fixture"),
        strategy="shared_pre_filter",
        on_record=snapshots.append,
    )
    assert len(result.records) == 51
    assert [len(item.records) for item in snapshots] == list(range(1, 52))
    assert not result.runtime_failed and not result.interrupted
    assert fixture.events.count("login") == 48
    assert fixture.events.count("query") == 51
    measured = build_access_benchmark(
        provenance(),
        fixture.workspace.bindings,
        result.records,
        strategy="shared_pre_filter",
        collection_names=fixture.workspace.store.collection_names(),
        run_id=RUN_ID,
    )
    assert measured.coverage_complete
    assert measured.exit_code == 0
    assert all(rate.primary_rate == 0.0 for rate in measured.rates)


@pytest.mark.parametrize("checkpoint_fails", [False, True])
def test_access_cohort_keeps_inflight_record_and_publishes_interruption_flags(
    checkpoint_fails: bool,
) -> None:
    from app.evaluation.access_runner import AccessCapture, execute_access_cohort
    from tests.unit.evaluation.test_access_reports import records

    cases = access_registry(access_inventory(), RUN_ID).cases
    raw = records()
    snapshots: list[Any] = []

    def capture(case: Any) -> Any:
        index = next(index for index, item in enumerate(cases) if item.id == case.id)
        return AccessCapture(raw[index], False, index == 1)

    def publish(snapshot: Any) -> None:
        snapshots.append(snapshot)
        if checkpoint_fails:
            raise OSError("ExceptionSecretMarker")

    result = execute_access_cohort(cases, capture, on_record=publish)
    assert len(result.records) == (1 if checkpoint_fails else 2)
    assert result.runtime_failed
    assert result.interrupted == (not checkpoint_fails)
    assert len(snapshots) == (1 if checkpoint_fails else 2)
    if not checkpoint_fails:
        assert snapshots[-1].interrupted and snapshots[-1].runtime_failed
