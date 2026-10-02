from collections.abc import Callable
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from qdrant_client import models
from sqlalchemy import Engine

from app.audits.contracts import AuditCase, Boundary
from app.audits.observer import AuditObserver
from app.audits.scoring import score_case
from app.chat.contracts import Generation
from app.retrieval.contracts import AuthorizedChunk
from app.retrieval.store import QdrantChunkStore
from tests.api.tenant_support import TenantApiSeed
from tests.api.test_chat import chat_client as chat_client
from tests.api.test_retrieval import indexed_document


def install_observer(
    client: TestClient,
    chunk_ids: tuple[UUID, ...],
    canaries: dict[str, str] | None = None,
) -> AuditObserver:
    observer = AuditObserver(
        case_id="OBS-001", canaries=canaries or {}, known_chunk_ids=chunk_ids
    )
    cast(FastAPI, client.app).state.observation_sink = observer
    return observer


def test_real_chat_records_all_boundaries_without_exporting_source_text(
    chat_client: TestClient,
    tenant_seed: TenantApiSeed,
    tenant_database_engines: tuple[Engine, Engine],
    vector_store: QdrantChunkStore,
    login_headers: Callable[[str, str], dict[str, str]],
) -> None:
    admin, _ = tenant_database_engines
    _, chunk_id = indexed_document(
        admin, vector_store, tenant_seed.organization_a_id, visibility="organization"
    )
    observer = install_observer(
        chat_client, (chunk_id,), {"source-canary": "Synthetic source evidence"}
    )
    headers = login_headers(tenant_seed.member_email, tenant_seed.organization_a_slug)
    response = chat_client.post(
        "/api/v1/chat/query",
        headers=headers,
        json={"question": "PrivateQuestionMarker"},
    )
    assert response.status_code == 200
    evidence = observer.snapshot()
    assert evidence.scope_hash is not None
    assert {stage.boundary for stage in evidence.boundaries} == set(Boundary)
    assert all(stage.state == "observed" for stage in evidence.boundaries)
    assert evidence.terminal == "answered"
    stages = {stage.boundary: stage for stage in evidence.boundaries}
    assert stages[Boundary.CONTEXT].chunk_ids == (chunk_id,)
    assert stages[Boundary.CONTEXT].canary_matches[0].canary_id == "source-canary"
    assert (
        stages[Boundary.CITATIONS_CANDIDATE].sequence
        < stages[Boundary.OUTPUT_DELIVERED].sequence
    )
    assert "Synthetic source evidence" not in evidence.model_dump_json()
    assert "PrivateQuestionMarker" not in evidence.model_dump_json()
    audit_case = AuditCase(
        id="OBS-001", expected_status=200, positive=True, required_chunks=(chunk_id,)
    )
    assert score_case(audit_case, evidence).status == "pass"


@pytest.mark.parametrize("drop_raw_observation", [False, True])
def test_real_unfiltered_qdrant_leak_is_seen_before_projection_rejection(
    chat_client: TestClient,
    tenant_seed: TenantApiSeed,
    tenant_database_engines: tuple[Engine, Engine],
    vector_store: QdrantChunkStore,
    login_headers: Callable[[str, str], dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    drop_raw_observation: bool,
) -> None:
    admin, _ = tenant_database_engines
    _, anchor = indexed_document(
        admin, vector_store, tenant_seed.organization_a_id, visibility="organization"
    )
    _, forbidden = indexed_document(
        admin, vector_store, tenant_seed.organization_b_id, visibility="organization"
    )
    observer = install_observer(chat_client, (anchor, forbidden))
    if drop_raw_observation:
        monkeypatch.setattr(observer, "retrieval", lambda *args: None)
    original = vector_store.client.query_points

    def unfiltered(*args: Any, **kwargs: Any) -> models.QueryResponse:
        kwargs["query_filter"] = None
        kwargs["prefetch"] = [
            branch.model_copy(update={"filter": None}) for branch in kwargs["prefetch"]
        ]
        return original(*args, **kwargs)

    monkeypatch.setattr(vector_store.client, "query_points", unfiltered)
    headers = login_headers(tenant_seed.member_email, tenant_seed.organization_a_slug)
    response = chat_client.post(
        "/api/v1/chat/query",
        headers=headers,
        json={"question": "evidence", "limit": 20},
    )
    assert response.status_code == 503
    assert response.json()["code"] == "invalid_retrieval_projection"
    evidence = observer.snapshot()
    stages = {stage.boundary: stage for stage in evidence.boundaries}
    if drop_raw_observation:
        assert stages[Boundary.RETRIEVAL_RAW].state == "unobserved"
    else:
        assert forbidden in stages[Boundary.RETRIEVAL_RAW].chunk_ids
    assert stages[Boundary.CONTEXT].state == "not_reached"
    result = score_case(
        AuditCase(id="OBS-001", expected_status=200, forbidden_chunks=(forbidden,)),
        evidence,
    )
    if drop_raw_observation:
        assert result.status == "inconclusive"
        assert result.first_exposure is None
        assert not result.coverage_complete
    else:
        assert result.status == "fail"
        assert result.first_exposure == Boundary.RETRIEVAL_RAW
        assert result.coverage_complete


def test_no_eligible_versions_records_skipped_query_not_an_empty_qdrant_result(
    chat_client: TestClient,
    tenant_seed: TenantApiSeed,
    login_headers: Callable[[str, str], dict[str, str]],
) -> None:
    observer = install_observer(chat_client, ())
    headers = login_headers(tenant_seed.member_email, tenant_seed.organization_a_slug)
    response = chat_client.post(
        "/api/v1/chat/query", headers=headers, json={"question": "evidence"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "abstained"
    evidence = observer.snapshot()
    stages = {stage.boundary: stage for stage in evidence.boundaries}
    assert stages[Boundary.RETRIEVAL_RAW].state == "not_reached"
    assert stages[Boundary.RETRIEVAL_RAW].decision == "query_skipped"
    assert stages[Boundary.CONTEXT].state == "observed"
    assert stages[Boundary.OUTPUT_CANDIDATE].state == "not_reached"
    assert (
        score_case(AuditCase(id="OBS-001", expected_status=200), evidence).status
        == "pass"
    )


def test_candidate_output_is_observed_but_not_delivered_when_citations_fail(
    chat_client: TestClient,
    tenant_seed: TenantApiSeed,
    tenant_database_engines: tuple[Engine, Engine],
    vector_store: QdrantChunkStore,
    login_headers: Callable[[str, str], dict[str, str]],
) -> None:
    admin, _ = tenant_database_engines
    _, chunk_id = indexed_document(
        admin, vector_store, tenant_seed.organization_a_id, visibility="organization"
    )
    invalid_id = uuid4()
    marker = "CandidateSecretNeverDelivered"

    class InvalidProvider:
        def generate(
            self, question: str, context: tuple[AuthorizedChunk, ...]
        ) -> Generation:
            return Generation(marker, (invalid_id,))

    cast(FastAPI, chat_client.app).state.generation_provider = InvalidProvider()
    observer = install_observer(
        chat_client, (chunk_id, invalid_id), {"output-secret": marker}
    )
    headers = login_headers(tenant_seed.member_email, tenant_seed.organization_a_slug)
    response = chat_client.post(
        "/api/v1/chat/query", headers=headers, json={"question": "evidence"}
    )
    assert response.status_code == 502
    assert marker not in response.text
    evidence = observer.snapshot()
    stages = {stage.boundary: stage for stage in evidence.boundaries}
    assert (
        stages[Boundary.OUTPUT_CANDIDATE].canary_matches[0].canary_id == "output-secret"
    )
    assert stages[Boundary.OUTPUT_DELIVERED].state == "not_reached"
    assert stages[Boundary.CITATIONS_DELIVERED].chunk_ids == ()
    assert marker not in evidence.model_dump_json()


def test_provider_timeout_leaves_partial_observation_inconclusive(
    chat_client: TestClient,
    tenant_seed: TenantApiSeed,
    tenant_database_engines: tuple[Engine, Engine],
    vector_store: QdrantChunkStore,
    login_headers: Callable[[str, str], dict[str, str]],
) -> None:
    admin, _ = tenant_database_engines
    _, chunk_id = indexed_document(
        admin, vector_store, tenant_seed.organization_a_id, visibility="organization"
    )

    class TimeoutProvider:
        def generate(
            self, question: str, context: tuple[AuthorizedChunk, ...]
        ) -> Generation:
            raise TimeoutError("SensitiveProviderErrorMarker")

    cast(FastAPI, chat_client.app).state.generation_provider = TimeoutProvider()
    observer = install_observer(chat_client, (chunk_id,))
    headers = login_headers(tenant_seed.member_email, tenant_seed.organization_a_slug)
    response = chat_client.post(
        "/api/v1/chat/query", headers=headers, json={"question": "evidence"}
    )
    assert response.status_code == 504
    evidence = observer.snapshot()
    stages = {stage.boundary: stage for stage in evidence.boundaries}
    assert stages[Boundary.CONTEXT].state == "observed"
    assert stages[Boundary.OUTPUT_CANDIDATE].state == "unobserved"
    assert "SensitiveProviderErrorMarker" not in response.text
    assert "SensitiveProviderErrorMarker" not in evidence.model_dump_json()
    assert (
        score_case(AuditCase(id="OBS-001", expected_status=200), evidence).status
        == "inconclusive"
    )


def test_observer_callback_failure_cannot_be_reported_as_a_pass(
    chat_client: TestClient,
    tenant_seed: TenantApiSeed,
    tenant_database_engines: tuple[Engine, Engine],
    vector_store: QdrantChunkStore,
    login_headers: Callable[[str, str], dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin, _ = tenant_database_engines
    _, chunk_id = indexed_document(
        admin, vector_store, tenant_seed.organization_a_id, visibility="organization"
    )
    observer = install_observer(chat_client, (chunk_id,))

    def unavailable(*args: object, **kwargs: object) -> None:
        raise RuntimeError("SensitiveObserverErrorMarker")

    monkeypatch.setattr(observer, "retrieval", unavailable)
    headers = login_headers(tenant_seed.member_email, tenant_seed.organization_a_slug)
    response = chat_client.post(
        "/api/v1/chat/query", headers=headers, json={"question": "evidence"}
    )
    assert response.status_code == 503
    evidence = observer.snapshot()
    assert "SensitiveObserverErrorMarker" not in response.text
    assert "SensitiveObserverErrorMarker" not in evidence.model_dump_json()
    assert (
        score_case(AuditCase(id="OBS-001", expected_status=200), evidence).status
        == "inconclusive"
    )
