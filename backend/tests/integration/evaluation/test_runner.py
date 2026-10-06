import os
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from sqlalchemy import text

from app.audits.contracts import Boundary, Terminal
from app.audits.seeding import FixtureCitingProvider, seed_workspace
from app.audits.workspace import AuditConfiguration, AuditWorkspace
from app.chat.contracts import Generation
from app.evaluation.dataset import generate_utility_corpus
from app.evaluation.models import PinnedEmbeddingProvider
from app.evaluation.workspace import utility_template
from app.retrieval.contracts import AuthorizedChunk
from tests.integration.audits.support import audit_config as audit_config
from tests.integration.evaluation.test_utility_workspace import (
    OwnedBenchmark,
)
from tests.integration.evaluation.test_utility_workspace import (
    benchmark as benchmark,
)


@pytest.mark.parametrize("benchmark", ["shared_pre_filter"], indirect=True)
def test_runner_captures_real_chat_and_timeout_with_fresh_authentication(
    benchmark: OwnedBenchmark,
) -> None:
    from app.evaluation.runner import capture_utility_query

    root = os.environ.get("RAGELIT_BENCHMARK_EMBEDDING_ROOT")
    if root is None:
        pytest.skip("requires explicitly supplied pinned local embedding assets")
    provider = PinnedEmbeddingProvider(Path(root))
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

    class TimeoutProvider:
        def generate(
            self, question: str, context: tuple[AuthorizedChunk, ...]
        ) -> Generation:
            raise TimeoutError

    with AuditWorkspace(benchmark.config, template, embeddings=provider) as workspace:
        try:
            seeded = seed_workspace(workspace, template)
        finally:
            benchmark.capture(workspace)
        app = cast(FastAPI, workspace.client.app)
        prior = (
            app.state.generation_provider,
            getattr(app.state, "observation_sink", None),
            app.state.chunk_store,
        )
        query = next(
            query
            for query in corpus.queries
            if query.relevant_document_ids == (public.id,)
        )
        with workspace.admin_engine.connect() as connection:
            before: int = connection.execute(
                text("SELECT count(*) FROM refresh_sessions")
            ).scalar_one()
        good = capture_utility_query(
            workspace, corpus, query, provider=FixtureCitingProvider()
        )
        failed = capture_utility_query(
            workspace, corpus, query, provider=TimeoutProvider()
        )
        with workspace.admin_engine.connect() as connection:
            after: int = connection.execute(
                text("SELECT count(*) FROM refresh_sessions")
            ).scalar_one()
        assert after == before + 2
        assert good.record.observation.terminal == Terminal.ANSWERED
        assert good.record.answer_label_match is True
        assert seeded.documents[public.id].chunk_ids[0] in {
            citation.chunk_id for citation in good.record.citations
        }
        assert good.record.observation.scope_hash is not None
        assert failed.record.observation.http_status == 504
        assert failed.record.error_code == "generation_timeout"
        assert failed.record.answer_label_match is None
        stages = {
            stage.boundary: stage for stage in failed.record.observation.boundaries
        }
        assert stages[Boundary.RETRIEVAL_ACCEPTED].state == "observed"
        assert stages[Boundary.CONTEXT].state == "observed"
        assert stages[Boundary.OUTPUT_DELIVERED].state == "unobserved"
        assert stages[Boundary.RETRIEVAL_ACCEPTED].duration_ms > 0
        assert (
            app.state.generation_provider,
            app.state.observation_sink,
            app.state.chunk_store,
        ) == prior
        content = good.record.model_dump_json()
        assert all(
            marker not in content
            for marker in ("Bearer ", '"question":', '"answer":', '"text":')
        )
