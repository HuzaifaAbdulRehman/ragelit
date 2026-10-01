from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from qdrant_client import models
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.documents.models import Document, DocumentVersion, IngestionJob
from app.retrieval.embeddings import Embedding
from app.retrieval.store import QdrantChunkStore
from app.tenancy.rls import set_request_context
from app.workers.ingestion import claim_job, run_once
from tests.api.document_support import TestEmbeddings
from tests.api.tenant_support import TenantApiSeed


def _upload(
    client: TestClient, headers: dict[str, str], root: Path, body: bytes
) -> UUID:
    state = cast(FastAPI, client.app).state
    state.settings = state.settings.model_copy(update={"data_dir": root})
    response = client.post(
        "/api/v1/documents?filename=evidence.txt",
        headers={**headers, "Content-Type": "text/plain"},
        content=body,
    )
    assert response.status_code == 202
    return UUID(response.json()["id"])


def test_worker_publishes_complete_document_to_real_qdrant(
    tenant_client: TestClient,
    tenant_seed: TenantApiSeed,
    tmp_path: Path,
    tenant_database_engines: tuple[Engine, Engine],
    login_headers: Callable[[str, str], dict[str, str]],
    vector_store: QdrantChunkStore,
) -> None:
    headers = login_headers(tenant_seed.owner_email, tenant_seed.organization_a_slug)
    document_id = _upload(tenant_client, headers, tmp_path, b"Project evidence " * 200)
    _, runtime = tenant_database_engines
    factory = sessionmaker(bind=runtime, expire_on_commit=False)
    settings = cast(FastAPI, tenant_client.app).state.settings
    assert run_once(
        tenant_seed.organization_a_id,
        factory=factory,
        settings=settings,
        store=vector_store,
        embeddings=TestEmbeddings(),
    )
    with factory() as session:
        set_request_context(
            session, user_id=uuid4(), organization_id=tenant_seed.organization_a_id
        )
        document = session.get(Document, document_id)
        assert document is not None and document.state == "ready"
        version = session.scalar(
            select(DocumentVersion).where(DocumentVersion.document_id == document_id)
        )
        assert version is not None and version.chunk_count > 1
        points, _ = vector_store.client.scroll(vector_store.collection_name, limit=100)
        assert len(points) == version.chunk_count
        assert all(
            point.payload and point.payload["active"] is True for point in points
        )
        assert all(
            point.payload and point.payload["visibility"] == "restricted"
            for point in points
        )
    assert not run_once(
        tenant_seed.organization_a_id,
        factory=factory,
        settings=settings,
        store=vector_store,
        embeddings=TestEmbeddings(),
    )


def test_job_is_claimed_once_and_expired_claim_is_replaced(
    tenant_client: TestClient,
    tenant_seed: TenantApiSeed,
    tmp_path: Path,
    tenant_database_engines: tuple[Engine, Engine],
    login_headers: Callable[[str, str], dict[str, str]],
) -> None:
    headers = login_headers(tenant_seed.owner_email, tenant_seed.organization_a_slug)
    _upload(tenant_client, headers, tmp_path, uuid4().hex.encode())
    _, runtime = tenant_database_engines
    now = datetime.now(UTC)
    with Session(runtime) as first:
        claim = claim_job(tenant_seed.organization_a_id, session=first, now=now)
        assert claim is not None
    with Session(runtime) as second:
        assert claim_job(tenant_seed.organization_a_id, session=second, now=now) is None
        replacement = claim_job(
            tenant_seed.organization_a_id, session=second, now=now + timedelta(hours=2)
        )
        assert replacement is not None
        assert replacement.version_id == claim.version_id
        assert replacement.claim_id != claim.claim_id


def test_invalid_content_fails_without_queryable_points(
    tenant_client: TestClient,
    tenant_seed: TenantApiSeed,
    tmp_path: Path,
    tenant_database_engines: tuple[Engine, Engine],
    login_headers: Callable[[str, str], dict[str, str]],
    vector_store: QdrantChunkStore,
) -> None:
    headers = login_headers(tenant_seed.owner_email, tenant_seed.organization_a_slug)
    document_id = _upload(tenant_client, headers, tmp_path, b"\x00bad")
    _, runtime = tenant_database_engines
    factory = sessionmaker(bind=runtime, expire_on_commit=False)
    assert run_once(
        tenant_seed.organization_a_id,
        factory=factory,
        settings=cast(FastAPI, tenant_client.app).state.settings,
        store=vector_store,
        embeddings=TestEmbeddings(),
    )
    with factory() as session:
        set_request_context(
            session, user_id=uuid4(), organization_id=tenant_seed.organization_a_id
        )
        document = session.get(Document, document_id)
        assert document is not None and document.state == "failed"
        job = session.scalar(select(IngestionJob))
        assert job is not None and job.error_code == "invalid_document"
    assert (
        vector_store.client.count(
            vector_store.collection_name,
            count_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="active", match=models.MatchValue(value=True)
                    )
                ]
            ),
            exact=True,
        ).count
        == 0
    )


def test_replaced_claim_cannot_activate_points(
    tenant_client: TestClient,
    tenant_seed: TenantApiSeed,
    tmp_path: Path,
    tenant_database_engines: tuple[Engine, Engine],
    login_headers: Callable[[str, str], dict[str, str]],
    vector_store: QdrantChunkStore,
) -> None:
    headers = login_headers(tenant_seed.owner_email, tenant_seed.organization_a_slug)
    _upload(tenant_client, headers, tmp_path, uuid4().hex.encode())
    _, runtime = tenant_database_engines
    factory = sessionmaker(bind=runtime, expire_on_commit=False)

    class SupersededEmbeddings(TestEmbeddings):
        def documents(self, texts: list[str]) -> list[Embedding]:
            with factory() as session:
                assert (
                    claim_job(
                        tenant_seed.organization_a_id,
                        session=session,
                        now=datetime.now(UTC) + timedelta(hours=2),
                    )
                    is not None
                )
            return super().documents(texts)

    assert run_once(
        tenant_seed.organization_a_id,
        factory=factory,
        settings=cast(FastAPI, tenant_client.app).state.settings,
        store=vector_store,
        embeddings=SupersededEmbeddings(),
    )
    points, _ = vector_store.client.scroll(vector_store.collection_name)
    assert points and all(
        point.payload and point.payload["active"] is False for point in points
    )
    with factory() as session:
        set_request_context(
            session, user_id=uuid4(), organization_id=tenant_seed.organization_a_id
        )
        job = session.scalar(select(IngestionJob))
        assert job is not None and job.state == "processing" and job.attempts == 2


def test_partial_indexing_failure_never_activates_earlier_batches(
    tenant_client: TestClient,
    tenant_seed: TenantApiSeed,
    tmp_path: Path,
    tenant_database_engines: tuple[Engine, Engine],
    login_headers: Callable[[str, str], dict[str, str]],
    vector_store: QdrantChunkStore,
) -> None:
    headers = login_headers(tenant_seed.owner_email, tenant_seed.organization_a_slug)
    _upload(tenant_client, headers, tmp_path, b"Safe source paragraph " * 5000)
    _, runtime = tenant_database_engines
    factory = sessionmaker(bind=runtime, expire_on_commit=False)

    class InterruptedEmbeddings(TestEmbeddings):
        batches = 0

        def documents(self, texts: list[str]) -> list[Embedding]:
            self.batches += 1
            if self.batches == 2:
                raise RuntimeError("source bodies must never reach job errors")
            return super().documents(texts)

    assert run_once(
        tenant_seed.organization_a_id,
        factory=factory,
        settings=cast(FastAPI, tenant_client.app).state.settings,
        store=vector_store,
        embeddings=InterruptedEmbeddings(),
    )
    points, _ = vector_store.client.scroll(vector_store.collection_name, limit=100)
    assert len(points) == 64
    assert all(point.payload and point.payload["active"] is False for point in points)
    with factory() as session:
        set_request_context(
            session, user_id=uuid4(), organization_id=tenant_seed.organization_a_id
        )
        job = session.scalar(select(IngestionJob))
        assert job is not None and job.state == "failed"
        assert job.error_code == "ingestion_failed"
