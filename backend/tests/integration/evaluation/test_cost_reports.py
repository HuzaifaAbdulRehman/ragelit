import json
import os
from pathlib import Path

import pytest

from app.audits.seeding import seed_workspace
from app.audits.workspace import AuditConfiguration, AuditWorkspace
from app.evaluation.cli import collect_provenance, main
from app.evaluation.cost_reports import (
    build_cost_report,
    validate_cost_report,
    write_cost_report,
)
from app.evaluation.costs import (
    IngestionTiming,
    collect_storage,
    index_build_measurement,
)
from app.evaluation.dataset import generate_utility_corpus
from app.evaluation.models import PinnedEmbeddingProvider
from app.evaluation.reports import GenerationRecord
from app.evaluation.workspace import utility_template
from tests.integration.audits.support import audit_config as audit_config
from tests.integration.evaluation.test_utility_workspace import OwnedBenchmark
from tests.integration.evaluation.test_utility_workspace import benchmark as benchmark


@pytest.mark.parametrize("benchmark", ["shared_pre_filter"], indirect=True)
def test_actual_partial_costs_replay_without_fabricated_utility_bindings(
    benchmark: OwnedBenchmark,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = os.environ.get("RAGELIT_BENCHMARK_EMBEDDING_ROOT")
    container = os.environ.get("RAGELIT_BENCHMARK_QDRANT_CONTAINER")
    if root is None or container is None:
        pytest.skip("requires explicit pinned embeddings and owned Qdrant container")
    embeddings = PinnedEmbeddingProvider(Path(root))
    benchmark.config = AuditConfiguration.model_validate(
        benchmark.config.model_dump()
        | {
            "embedding_fingerprint": embeddings.fingerprint,
            "embedding_dimension": embeddings.dimension,
        }
    )
    original = utility_template(generate_utility_corpus())
    template = original.model_copy(
        update={
            "organizations": original.organizations[:1],
            "groups": tuple(
                group for group in original.groups if group.organization_id == "org-1"
            ),
            "actors": tuple(
                actor for actor in original.actors if actor.organization_id == "org-1"
            ),
            "documents": original.documents[:1],
        }
    )
    timings: list[IngestionTiming] = []

    def observe(identifier: str, duration_ms: float, ready: bool) -> None:
        timings.append(
            IngestionTiming(
                document_id=identifier, duration_ms=duration_ms, ready=ready
            )
        )

    with AuditWorkspace(benchmark.config, template, embeddings=embeddings) as workspace:
        benchmark.capture(workspace)
        seed_workspace(workspace, template, on_ingestion=observe)
        storage = collect_storage(workspace, qdrant_container=container)
        measured = build_cost_report(
            collect_provenance(workspace, GenerationRecord(mode="fixture")),
            strategy="shared_pre_filter",
            collection_names=workspace.store.collection_names(),
            index=index_build_measurement(tuple(timings)),
            storage=storage,
        )
        path = write_cost_report(
            measured, workspace.config.report_dir / "utility-costs"
        )
        assert validate_cost_report(path) == measured
        assert measured.utility is None
        assert measured.summary.index_expected == 87
        assert measured.summary.index_recorded == 1
        assert measured.summary.index_build_ms is None
        assert measured.summary.observed_ingestion_ms is not None
        assert measured.summary.observed_ingestion_ms > 0
        assert measured.summary.upload_bytes == len(template.documents[0].text.encode())
        assert (
            measured.summary.database_bytes is not None
            and measured.summary.database_bytes > 0
        )
        assert measured.summary.qdrant_collection_disk_bytes is not None
        assert measured.summary.qdrant_collection_disk_bytes > 0
        assert measured.summary.revocation_recorded == 0
        assert measured.summary.revocation_mean_ms is None
        assert not measured.coverage_complete
        assert measured.exit_code == 2
        capsys.readouterr()
        assert main(["--validate-cost-report", str(path)]) == 0
        assert json.loads(capsys.readouterr().out) == {
            "code": "utility_cost_artifact_valid",
            "exit_code": 0,
            "run_exit_code": 2,
            "coverage_complete": False,
        }
        workspace.validate_owned()
