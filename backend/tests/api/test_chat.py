from collections.abc import Callable
from typing import cast
from uuid import UUID, uuid4

import pytest
from app.chat.contracts import Generation
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.retrieval.contracts import AuthorizedChunk
from app.retrieval.store import QdrantChunkStore
from tests.api.document_support import TestEmbeddings
from tests.api.tenant_support import TenantApiSeed
from tests.api.test_retrieval import indexed_document


class CitingProvider:
    def generate(
        self, question: str, context: tuple[AuthorizedChunk, ...]
    ) -> Generation:
        return Generation(
            "An answer grounded in the supplied evidence.", (context[0].id,)
        )


@pytest.fixture
def chat_client(
    tenant_client: TestClient, vector_store: QdrantChunkStore
) -> TestClient:
    state = cast(FastAPI, tenant_client.app).state
    state.chunk_store, state.embeddings = vector_store, TestEmbeddings()
    state.generation_provider = CitingProvider()
    return tenant_client


def test_answer_citations_and_trace_preserve_evidence_without_source_bodies(
    chat_client: TestClient,
    tenant_seed: TenantApiSeed,
    tenant_database_engines: tuple[Engine, Engine],
    login_headers: Callable[[str, str], dict[str, str]],
    vector_store: QdrantChunkStore,
) -> None:
    admin, _ = tenant_database_engines
    document_id, chunk_id = indexed_document(
        admin, vector_store, tenant_seed.organization_a_id, visibility="organization"
    )
    headers = login_headers(tenant_seed.member_email, tenant_seed.organization_a_slug)
    response = chat_client.post(
        "/api/v1/chat/query",
        headers=headers,
        json={"question": "QuestionFullPrivateMarker"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "answered"
    citation = response.json()["citations"][0]
    assert citation["chunk_id"] == str(chunk_id)
    assert citation["document_id"] == str(document_id)
    assert citation["location"] == "line 1"
    trace = chat_client.get(
        f"/api/v1/query-runs/{response.json()['query_run_id']}", headers=headers
    )
    assert trace.status_code == 200
    assert {stage["stage"] for stage in trace.json()["stages"]} == {
        "retrieval",
        "context",
        "generation",
        "citations",
    }
    assert "Synthetic source evidence" not in trace.text
    assert "QuestionFullPrivateMarker" not in trace.text
    assert str(chunk_id) in trace.text


def test_no_permitted_evidence_abstains_without_calling_provider(
    chat_client: TestClient,
    tenant_seed: TenantApiSeed,
    tenant_database_engines: tuple[Engine, Engine],
    login_headers: Callable[[str, str], dict[str, str]],
    vector_store: QdrantChunkStore,
) -> None:
    class UnreachableProvider:
        def generate(
            self, question: str, context: tuple[AuthorizedChunk, ...]
        ) -> Generation:
            raise AssertionError("a model must not be called without evidence")

    cast(FastAPI, chat_client.app).state.generation_provider = UnreachableProvider()
    admin, _ = tenant_database_engines
    indexed_document(
        admin, vector_store, tenant_seed.organization_b_id, visibility="organization"
    )
    headers = login_headers(tenant_seed.member_email, tenant_seed.organization_a_slug)
    response = chat_client.post(
        "/api/v1/chat/query", headers=headers, json={"question": "evidence"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "abstained"
    assert response.json()["answer"] is None
    assert response.json()["citations"] == []


@pytest.mark.parametrize(
    "mode,status,code",
    [("timeout", 504, "generation_timeout"), ("citation", 502, "invalid_citations")],
)
def test_generation_failure_preserves_retrieval_and_context_trace(
    chat_client: TestClient,
    tenant_seed: TenantApiSeed,
    tenant_database_engines: tuple[Engine, Engine],
    login_headers: Callable[[str, str], dict[str, str]],
    vector_store: QdrantChunkStore,
    mode: str,
    status: int,
    code: str,
) -> None:
    class InvalidProvider:
        def generate(
            self, question: str, context: tuple[AuthorizedChunk, ...]
        ) -> Generation:
            if mode == "timeout":
                raise TimeoutError("provider details must not reach the response")
            return Generation("An unsupported answer.", (uuid4(),))

    cast(FastAPI, chat_client.app).state.generation_provider = InvalidProvider()
    admin, _ = tenant_database_engines
    _, chunk_id = indexed_document(
        admin, vector_store, tenant_seed.organization_a_id, visibility="organization"
    )
    headers = login_headers(tenant_seed.member_email, tenant_seed.organization_a_slug)
    response = chat_client.post(
        "/api/v1/chat/query", headers=headers, json={"question": "evidence"}
    )
    assert response.status_code == status
    assert response.json()["code"] == code
    assert "provider details" not in response.text
    run_id = response.json()["query_run_id"]
    trace = chat_client.get(f"/api/v1/query-runs/{run_id}", headers=headers)
    assert trace.status_code == 200 and trace.json()["state"] == "failed"
    stages = {stage["stage"]: stage for stage in trace.json()["stages"]}
    assert str(chunk_id) in stages["retrieval"]["chunk_ids"]
    assert str(chunk_id) in stages["context"]["chunk_ids"]


def test_trace_is_private_to_its_actor_and_organization(
    chat_client: TestClient,
    tenant_seed: TenantApiSeed,
    login_headers: Callable[[str, str], dict[str, str]],
) -> None:
    member = login_headers(tenant_seed.member_email, tenant_seed.organization_a_slug)
    response = chat_client.post(
        "/api/v1/chat/query", headers=member, json={"question": "evidence"}
    )
    run_id = response.json()["query_run_id"]
    UUID(run_id)
    for email, slug in (
        (tenant_seed.owner_email, tenant_seed.organization_a_slug),
        (tenant_seed.outsider_email, tenant_seed.organization_b_slug),
    ):
        other = login_headers(email, slug)
        assert (
            chat_client.get(f"/api/v1/query-runs/{run_id}", headers=other).status_code
            == 404
        )
