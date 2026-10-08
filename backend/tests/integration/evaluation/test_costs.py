import os
from typing import Any

import pytest

from app.audits.seeding import seed_workspace
from app.audits.workspace import AuditWorkspace
from app.evaluation.dataset import generate_utility_corpus
from app.evaluation.workspace import utility_template
from tests.integration.audits.support import audit_config as audit_config
from tests.integration.evaluation.test_utility_workspace import OwnedBenchmark
from tests.integration.evaluation.test_utility_workspace import benchmark as benchmark
from tests.unit.evaluation.workspace_support import MissEmbeddings


@pytest.mark.parametrize("benchmark", ["shared_pre_filter"], indirect=True)
def test_owned_ingestion_costs_and_storage_exclude_reused_work(
    benchmark: OwnedBenchmark,
) -> None:
    from app.evaluation.costs import (
        IngestionTiming,
        collect_storage,
        index_build_measurement,
    )

    corpus = generate_utility_corpus()
    original = utility_template(corpus)
    template = original.model_copy(
        update={
            "organizations": original.organizations[:1],
            "groups": tuple(
                group for group in original.groups if group.organization_id == "org-1"
            ),
            "actors": tuple(
                actor for actor in original.actors if actor.organization_id == "org-1"
            ),
            "documents": original.documents[:3],
        }
    )
    timings: list[IngestionTiming] = []

    def observe(identifier: str, duration_ms: float, ready: bool) -> None:
        timings.append(
            IngestionTiming(
                document_id=identifier, duration_ms=duration_ms, ready=ready
            )
        )

    def unexpected(*args: Any) -> None:
        pytest.fail("reused seeding emitted an ingestion measurement")

    with AuditWorkspace(
        benchmark.config, template, embeddings=MissEmbeddings()
    ) as workspace:
        benchmark.capture(workspace)
        seed_workspace(workspace, template, on_ingestion=observe)
        assert [event.document_id for event in timings] == [
            "org-1-change-notice",
            "org-1-support-response",
            "org-1-visitor-booking",
        ]
        assert all(event.ready and event.duration_ms > 0 for event in timings)
        measured = index_build_measurement(tuple(timings))
        assert measured.recorded_documents == 3
        assert measured.index_build_ms is None
        assert not measured.coverage_complete
        storage = collect_storage(workspace)
        assert storage.database_bytes > 0
        assert storage.upload_bytes == sum(
            len(doc.text.encode()) for doc in template.documents
        )
        assert storage.collection_count == 1
        assert storage.qdrant_collection_disk_bytes is None
        assert not storage.coverage_complete
        if container := os.environ.get("RAGELIT_BENCHMARK_QDRANT_CONTAINER"):
            disk = collect_storage(workspace, qdrant_container=container)
            assert disk.coverage_complete
            assert disk.qdrant_collection_disk_bytes is not None
            assert disk.qdrant_collection_disk_bytes > 0
            assert tuple(item.name for item in disk.disks) == (benchmark.config.name,)
            assert disk.upload_bytes == storage.upload_bytes
        seed_workspace(workspace, template, on_ingestion=unexpected)
        assert len(timings) == 3
