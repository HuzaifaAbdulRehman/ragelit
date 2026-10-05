import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from fastapi import FastAPI
from qdrant_client import QdrantClient, models
from sqlalchemy import select, text

from app.audits.fixtures import generate_fixtures
from app.audits.seeding import FixtureCitingProvider, login_actor, seed_workspace
from app.audits.workspace import AuditConfiguration, AuditWorkspace, AuditWorkspaceError
from app.documents.models import DocumentVersion
from app.evaluation.dataset import generate_utility_corpus
from app.evaluation.models import PinnedEmbeddingProvider
from app.evaluation.workspace import utility_template
from app.retrieval.embeddings import Embedding
from tests.integration.audits.support import audit_config as audit_config
from tests.unit.evaluation.workspace_support import MissEmbeddings


@dataclass
class OwnedBenchmark:
    config: AuditConfiguration
    collections: set[str] = field(default_factory=set)

    def capture(self, workspace: AuditWorkspace) -> None:
        self.collections.update(workspace.store.collection_names())


@pytest.fixture(params=["shared_pre_filter", "tenant_collections"])
def benchmark(
    audit_config: AuditConfiguration, request: pytest.FixtureRequest
) -> Iterator[OwnedBenchmark]:
    config = AuditConfiguration.model_validate(
        audit_config.model_dump()
        | {
            "vector_strategy": request.param,
            "embedding_fingerprint": MissEmbeddings.fingerprint,
            "embedding_dimension": MissEmbeddings.dimension,
        }
    )
    resources = OwnedBenchmark(config)
    try:
        yield resources
    finally:
        config.validate_paths()
        client = QdrantClient(url=config.qdrant_url, trust_env=False)
        try:
            for name in sorted(resources.collections):
                if name == config.name:
                    continue
                prefix = f"{config.name}_tenant_"
                assert name.startswith(prefix)
                identifier = UUID(hex=name.removeprefix(prefix))
                assert name == f"{prefix}{identifier.hex}"
                if client.collection_exists(name):
                    client.delete_collection(name)
        finally:
            client.close()


def test_bound_provider_is_used_by_the_app_and_reopen_rejects_drift(
    benchmark: OwnedBenchmark,
) -> None:
    config = benchmark.config
    provider = MissEmbeddings()
    template = utility_template(generate_utility_corpus())
    with AuditWorkspace(config, template, embeddings=provider) as workspace:
        benchmark.capture(workspace)
        assert cast(FastAPI, workspace.client.app).state.embeddings is provider
        with workspace.factory() as session:
            assert (
                session.execute(text("SELECT current_user")).scalar_one()
                == config.application_role
            )
            assert (
                session.execute(text("SELECT count(*) FROM memberships")).scalar_one()
                == 0
            )
        before = config.bindings_path.read_bytes()
    changed = AuditConfiguration.model_validate(
        config.model_dump() | {"embedding_fingerprint": "2" * 64}
    )
    changed_provider = MissEmbeddings()
    changed_provider.fingerprint = "2" * 64
    with pytest.raises(AuditWorkspaceError, match="audit_marker_drift"):
        with AuditWorkspace(changed, template, embeddings=changed_provider):
            pytest.fail("workspace reopened with different model identity")
    assert config.bindings_path.read_bytes() == before
    with AuditWorkspace(config, template, embeddings=provider) as workspace:
        assert workspace.bindings.workspace_id == config.workspace_id
        assert config.bindings_path.read_bytes() == before


def test_utility_seed_keeps_a_bad_retrieval_result_instead_of_excluding_it(
    benchmark: OwnedBenchmark,
) -> None:
    corpus = generate_utility_corpus()
    template = utility_template(corpus)
    public = next(doc for doc in template.documents if doc.visibility == "organization")
    group = next(doc for doc in template.documents if doc.group_ids)
    direct = next(doc for doc in template.documents if doc.user_ids)
    template = template.model_copy(update={"documents": (public, group, direct)})
    config = benchmark.config
    provider = MissEmbeddings()
    with AuditWorkspace(config, template, embeddings=provider) as workspace:
        try:
            bindings = seed_workspace(workspace, template)
        finally:
            benchmark.capture(workspace)
        assert bindings.seeded
        assert set(bindings.documents) == {public.id, group.id, direct.id}
        with workspace.admin_engine.connect() as connection:
            states = connection.execute(
                select(DocumentVersion.state, DocumentVersion.chunk_count)
            ).all()
            assert states == [("ready", 1)] * 3
        collections = workspace.store.collection_names()
        assert len(collections) == (
            1 if config.vector_strategy == "shared_pre_filter" else 3
        )
        assert (
            sum(
                workspace.store.client.count(name, exact=True).count
                for name in collections
            )
            == 3
        )
        for name in collections:
            info = workspace.store.client.get_collection(name)
            dense = info.config.params.vectors
            assert isinstance(dense, dict)
            assert dense["dense"].size == 384
            sparse = info.config.params.sparse_vectors
            assert (
                sparse is not None and sparse["sparse"].modifier == models.Modifier.IDF
            )
        actor = next(
            actor for actor in template.actors if actor.id == public.control_actor_id
        )
        organization = next(
            org for org in template.organizations if org.id == actor.organization_id
        )
        headers = login_actor(
            workspace, email=actor.email, organization_slug=organization.slug
        )
        with workspace.mutation():
            response = workspace.client.post(
                "/api/v1/chat/query",
                headers=headers,
                json={"question": public.question, "limit": 10},
            )
        assert response.status_code == 200
        assert response.json()["status"] == "abstained"
        assert response.json()["answer"] is None
        assert response.json()["citations"] == []
        before = workspace.bindings.checksum
        assert seed_workspace(workspace, template).checksum == before
        assert len(workspace.bindings.documents) == 3
        assert set(workspace.bindings.documents) == set(bindings.documents)
        assert workspace.bindings.checksum == before


@pytest.mark.parametrize("benchmark", ["shared_pre_filter"], indirect=True)
def test_access_seed_still_requires_its_positive_retrieval_probe(
    benchmark: OwnedBenchmark,
) -> None:
    original = generate_fixtures()
    template = original.model_copy(update={"documents": (original.documents[0],)})
    with AuditWorkspace(
        benchmark.config, template, embeddings=MissEmbeddings()
    ) as workspace:
        try:
            with pytest.raises(AuditWorkspaceError, match="audit_probe_unavailable"):
                seed_workspace(workspace, template)
        finally:
            benchmark.capture(workspace)
        assert not workspace.bindings.seeded
        assert len(workspace.bindings.documents) == 1


@pytest.mark.parametrize("benchmark", ["shared_pre_filter"], indirect=True)
def test_utility_seed_does_not_bypass_failed_ingestion(
    benchmark: OwnedBenchmark,
) -> None:
    class FailedEmbeddings(MissEmbeddings):
        def documents(self, texts: list[str]) -> list[Embedding]:
            raise ValueError("synthetic test embedding failure")

    original = utility_template(generate_utility_corpus())
    template = original.model_copy(update={"documents": (original.documents[0],)})
    with AuditWorkspace(
        benchmark.config, template, embeddings=FailedEmbeddings()
    ) as workspace:
        try:
            with pytest.raises(AuditWorkspaceError, match="audit_ingestion_failed"):
                seed_workspace(workspace, template)
        finally:
            benchmark.capture(workspace)
        assert not workspace.bindings.seeded
        assert workspace.bindings.documents == {}
        with workspace.admin_engine.connect() as connection:
            assert connection.execute(select(DocumentVersion.state)).all() == [
                ("failed",)
            ]


@pytest.mark.parametrize("benchmark", ["shared_pre_filter"], indirect=True)
def test_pinned_models_flow_through_production_ingestion_retrieval_and_chat(
    benchmark: OwnedBenchmark,
) -> None:
    model_root = os.environ.get("RAGELIT_BENCHMARK_EMBEDDING_ROOT")
    if model_root is None:
        pytest.skip("requires explicitly supplied pinned local embedding assets")
    provider = PinnedEmbeddingProvider(Path(model_root))
    benchmark.config = AuditConfiguration.model_validate(
        benchmark.config.model_dump()
        | {
            "embedding_fingerprint": provider.fingerprint,
            "embedding_dimension": provider.dimension,
        }
    )
    corpus = generate_utility_corpus()
    original = utility_template(corpus)
    public = next(doc for doc in original.documents if doc.visibility == "organization")
    group = next(doc for doc in original.documents if doc.group_ids)
    direct = next(doc for doc in original.documents if doc.user_ids)
    template = original.model_copy(update={"documents": (public, group, direct)})
    with AuditWorkspace(benchmark.config, template, embeddings=provider) as workspace:
        try:
            bindings = seed_workspace(workspace, template)
        finally:
            benchmark.capture(workspace)
        app = cast(FastAPI, workspace.client.app)
        assert app.state.embeddings is provider
        assert isinstance(app.state.generation_provider, FixtureCitingProvider)
        assert set(bindings.documents) == {public.id, group.id, direct.id}
        assert (
            workspace.store.client.count(benchmark.config.name, exact=True).count == 3
        )
        actor = next(
            actor for actor in template.actors if actor.id == public.control_actor_id
        )
        organization = next(
            org for org in template.organizations if org.id == actor.organization_id
        )
        headers = login_actor(
            workspace, email=actor.email, organization_slug=organization.slug
        )
        with workspace.mutation():
            response = workspace.client.post(
                "/api/v1/chat/query",
                headers=headers,
                json={"question": public.question, "limit": 10},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "answered"
        expected = bindings.documents[public.id]
        assert str(expected.document_id) in {
            citation["document_id"] for citation in body["citations"]
        }
        assert str(expected.version_id) in {
            citation["version_id"] for citation in body["citations"]
        }
        assert str(expected.chunk_ids[0]) in {
            citation["chunk_id"] for citation in body["citations"]
        }
        label = next(
            doc.expected_answer for doc in corpus.documents if doc.id == public.id
        )
        assert isinstance(body["answer"], str)
        assert label in body["answer"]
