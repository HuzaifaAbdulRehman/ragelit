import hashlib
import json
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from app.audits.contracts import Boundary, Terminal
from app.evaluation.costs import (
    CollectionDiskMeasurement,
    IngestionTiming,
    index_build_measurement,
    storage_measurement,
)
from app.evaluation.dataset import generate_utility_corpus
from app.evaluation.reports import UtilityQueryRecord
from app.evaluation.revocations import revocation_measurement
from tests.unit.evaluation.report_support import bindings, provenance, record, report


@cache
def cost_inputs() -> dict[str, Any]:
    corpus = generate_utility_corpus()
    events = []
    for index, delay in ((0, 10.0), (29, 20.0), (58, 30.0)):
        before = record(index)
        after = UtilityQueryRecord.model_validate(
            before.model_copy(
                update={
                    "observation": before.observation.model_copy(
                        update={
                            "terminal": Terminal.ABSTAINED,
                            "boundaries": tuple(
                                stage.model_copy(
                                    update={
                                        "chunk_ids": (),
                                        "state": "observed"
                                        if stage.boundary
                                        in {
                                            Boundary.RETRIEVAL_RAW,
                                            Boundary.RETRIEVAL_ACCEPTED,
                                            Boundary.CONTEXT,
                                        }
                                        else "not_reached",
                                    }
                                )
                                for stage in before.observation.boundaries
                            ),
                        }
                    ),
                    "citations": (),
                    "answer_label_match": False,
                }
            ).model_dump()
        )
        events.append(
            revocation_measurement(
                bindings()[index],
                before,
                after,
                update_committed=True,
                elapsed_ms=delay,
                grant_restore="restored",
            )
        )
    return {
        "index": index_build_measurement(
            tuple(
                IngestionTiming(document_id=doc.id, duration_ms=2.0, ready=True)
                for doc in corpus.documents
            )
        ),
        "storage": storage_measurement(
            database_bytes=32768,
            upload_bytes=sum(len(doc.text.encode()) for doc in corpus.documents),
            collection_names=("ragelit_audit_test",),
            disks=(
                CollectionDiskMeasurement(name="ragelit_audit_test", allocated_kib=7),
            ),
        ),
        "revocations": tuple(events),
    }


def test_full_cost_report_replays_literal_cost_oracles() -> None:
    from app.evaluation.cost_reports import build_cost_report

    measured = build_cost_report(
        provenance(),
        strategy="shared_pre_filter",
        collection_names=("ragelit_audit_test",),
        utility=report(),
        **cost_inputs(),
    )
    assert measured.summary.index_build_ms == 174.0
    assert measured.summary.qdrant_collection_disk_bytes == 7168
    assert measured.summary.revocation_expected == 3
    assert measured.summary.revocation_recorded == 3
    assert measured.summary.revocation_measured == 3
    assert measured.summary.revocation_mean_ms == 20.0
    assert measured.costs_complete
    assert measured.coverage_complete
    assert measured.exit_code == 0


def test_setup_failure_preserves_partial_index_without_fabricated_bindings() -> None:
    from app.evaluation.cost_reports import build_cost_report

    first = generate_utility_corpus().documents[0]
    partial = index_build_measurement(
        (IngestionTiming(document_id=first.id, duration_ms=5.0, ready=True),),
        runtime_failed=True,
    )
    measured = build_cost_report(
        provenance(),
        strategy="shared_pre_filter",
        collection_names=("ragelit_audit_test",),
        index=partial,
        runtime_failed=True,
    )
    assert measured.utility is None
    assert measured.summary.index_recorded == 1
    assert measured.summary.index_build_ms is None
    assert measured.summary.observed_ingestion_ms == 5.0
    assert measured.summary.revocation_mean_ms is None
    assert measured.summary.revocation_measured == 0
    assert not measured.coverage_complete
    assert measured.exit_code == 2


def test_missing_revocation_keeps_primary_unknown_and_observed_denominator() -> None:
    from app.evaluation.cost_reports import build_cost_report

    inputs = cost_inputs() | {"revocations": cost_inputs()["revocations"][:2]}
    measured = build_cost_report(
        provenance(),
        strategy="shared_pre_filter",
        collection_names=("ragelit_audit_test",),
        utility=report(),
        **inputs,
    )
    assert measured.summary.revocation_expected == 3
    assert measured.summary.revocation_recorded == 2
    assert measured.summary.revocation_measured == 2
    assert measured.summary.revocation_mean_ms is None
    assert measured.summary.observed_revocation_mean_ms == 15.0
    assert measured.exit_code == 2


def test_provisional_and_partial_utility_cannot_claim_completed_cost_cohort() -> None:
    from app.evaluation.cost_reports import build_cost_report

    for utility, provisional in ((report(partial=True), False), (report(), True)):
        measured = build_cost_report(
            provenance(),
            strategy="shared_pre_filter",
            collection_names=("ragelit_audit_test",),
            utility=utility,
            provisional=provisional,
            **cost_inputs(),
        )
        assert measured.summary.index_build_ms == 174.0
        assert measured.exit_code == 2
        assert not measured.coverage_complete


def test_duplicate_or_wrong_order_revocation_events_are_rejected() -> None:
    from app.evaluation.cost_reports import build_cost_report

    events = cost_inputs()["revocations"]
    for changed in ((events[0], events[0]), (events[1],)):
        with pytest.raises(ValueError):
            build_cost_report(
                provenance(),
                strategy="shared_pre_filter",
                collection_names=("ragelit_audit_test",),
                utility=report(),
                **(cost_inputs() | {"revocations": changed}),
            )


def test_foreign_cost_scope_or_provenance_is_rejected() -> None:
    from app.evaluation.cost_reports import build_cost_report

    with pytest.raises(ValueError):
        build_cost_report(
            provenance(),
            strategy="shared_pre_filter",
            collection_names=("ragelit_audit_other",),
            utility=report(),
            **cost_inputs(),
        )
    with pytest.raises(ValueError):
        build_cost_report(
            provenance().model_copy(update={"git_dirty": True}),
            strategy="shared_pre_filter",
            collection_names=("ragelit_audit_test",),
            utility=report(),
            **cost_inputs(),
        )
    with pytest.raises(ValueError):
        build_cost_report(
            provenance(),
            strategy="shared_pre_filter",
            collection_names=("ragelit_audit_test",),
            **cost_inputs(),
        )


def test_cost_artifacts_replay_and_never_overwrite(tmp_path: Path) -> None:
    from app.evaluation.cost_reports import (
        build_cost_report,
        validate_cost_report,
        write_cost_report,
    )

    measured = build_cost_report(
        provenance(),
        strategy="shared_pre_filter",
        collection_names=("ragelit_audit_test",),
        utility=report(),
        **cost_inputs(),
    )
    path = write_cost_report(measured, tmp_path)
    assert validate_cost_report(path) == measured
    with pytest.raises(FileExistsError):
        write_cost_report(measured, tmp_path)


def test_forged_cost_summary_fails_even_with_updated_receipt(tmp_path: Path) -> None:
    from app.evaluation.cost_reports import (
        build_cost_report,
        validate_cost_report,
        write_cost_report,
    )

    measured = build_cost_report(
        provenance(),
        strategy="shared_pre_filter",
        collection_names=("ragelit_audit_test",),
        utility=report(),
        **cost_inputs(),
    )
    path = write_cost_report(measured, tmp_path)
    data = json.loads(path.read_bytes())
    data["summary"]["index_build_ms"] = 0.0
    content = json.dumps(data).encode()
    path.write_bytes(content)
    path.with_suffix(".sha256.json").write_text(
        json.dumps(
            {"filename": path.name, "sha256": hashlib.sha256(content).hexdigest()}
        )
    )
    with pytest.raises(ValueError):
        validate_cost_report(path)
