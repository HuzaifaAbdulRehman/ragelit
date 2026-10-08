import os
from pathlib import Path

import pytest
from sqlalchemy import select

from app.audits.contracts import Boundary, Terminal
from app.audits.seeding import FixtureCitingProvider, seed_workspace
from app.audits.workspace import AuditConfiguration, AuditWorkspace
from app.documents.models import Document
from app.evaluation.dataset import generate_utility_corpus
from app.evaluation.models import PinnedEmbeddingProvider
from app.evaluation.runner import capture_utility_query
from app.evaluation.workspace import utility_template
from tests.integration.audits.support import audit_config as audit_config
from tests.integration.evaluation.test_utility_workspace import OwnedBenchmark
from tests.integration.evaluation.test_utility_workspace import benchmark as benchmark


@pytest.mark.parametrize("benchmark", ["shared_pre_filter"], indirect=True)
def test_revocation_is_measured_through_chat_and_original_grants_are_restored(
    benchmark: OwnedBenchmark,
) -> None:
    from app.evaluation.revocations import capture_revocation

    root = os.environ.get("RAGELIT_BENCHMARK_EMBEDDING_ROOT")
    if root is None:
        pytest.skip("requires explicitly supplied pinned local embedding assets")
    embeddings = PinnedEmbeddingProvider(Path(root))
    benchmark.config = AuditConfiguration.model_validate(
        benchmark.config.model_dump()
        | {
            "embedding_fingerprint": embeddings.fingerprint,
            "embedding_dimension": embeddings.dimension,
        }
    )
    corpus = generate_utility_corpus()
    original = utility_template(corpus)
    document = original.documents[0]
    template = original.model_copy(
        update={
            "organizations": original.organizations[:1],
            "groups": tuple(
                group for group in original.groups if group.organization_id == "org-1"
            ),
            "actors": tuple(
                actor for actor in original.actors if actor.organization_id == "org-1"
            ),
            "documents": (document,),
        }
    )
    query = corpus.queries[0]
    with AuditWorkspace(benchmark.config, template, embeddings=embeddings) as workspace:
        benchmark.capture(workspace)
        seed_workspace(workspace, template)
        measured = capture_revocation(
            workspace, corpus, query, provider=FixtureCitingProvider()
        )
        assert measured.positive_before
        assert measured.update_committed
        assert measured.denial_observed
        assert measured.grant_restore == "restored"
        assert not measured.runtime_failed
        assert measured.coverage_complete
        assert measured.revocation_to_confirmation_ms is not None
        assert measured.revocation_to_confirmation_ms > 0
        assert measured.before.actor_id == query.actor_id
        assert measured.before.observation.terminal == Terminal.ANSWERED
        assert measured.after is not None
        assert measured.after.actor_id == query.actor_id
        assert measured.after.observation.terminal == Terminal.ABSTAINED
        for stage in measured.after.observation.boundaries:
            if stage.boundary in {Boundary.RETRIEVAL_ACCEPTED, Boundary.CONTEXT}:
                assert stage.state == "observed"
                assert stage.chunk_ids == ()
        with workspace.admin_engine.connect() as connection:
            visibility = connection.execute(
                select(Document.visibility).where(
                    Document.id == measured.target.document_id
                )
            ).scalar_one()
        assert visibility == "organization"
        restored = capture_utility_query(
            workspace, corpus, query, provider=FixtureCitingProvider()
        )
        assert restored.record.observation.terminal == Terminal.ANSWERED
        assert restored.record.answer_label_match is True
        assert measured.target.chunk_ids[0] in {
            item.chunk_id for item in restored.record.citations
        }
        workspace.validate_owned()
        content = measured.model_dump_json()
        assert all(
            marker not in content
            for marker in ("Bearer ", '"question":', '"answer":', '"text":')
        )
