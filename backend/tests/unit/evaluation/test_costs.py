import json
from subprocess import CompletedProcess
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.audits.workspace import AuditWorkspace
from app.evaluation.dataset import generate_utility_corpus


def test_fresh_index_requires_every_successful_ingestion_and_sums_actual_timings() -> (
    None
):
    from app.evaluation.costs import IngestionTiming, index_build_measurement

    timings = tuple(
        IngestionTiming(document_id=doc.id, duration_ms=2.0, ready=True)
        for doc in generate_utility_corpus().documents
    )
    measured = index_build_measurement(timings)
    assert measured.expected_documents == 87
    assert measured.recorded_documents == 87
    assert measured.coverage_complete
    assert measured.index_build_ms == 174.0
    assert measured.observed_ingestion_ms == 174.0
    assert not measured.reused


@pytest.mark.parametrize("failed", [False, True])
def test_partial_ingestion_keeps_elapsed_work_without_claiming_full_index(
    failed: bool,
) -> None:
    from app.evaluation.costs import IngestionTiming, index_build_measurement

    ids = [doc.id for doc in generate_utility_corpus().documents[:2]]
    measured = index_build_measurement(
        (
            IngestionTiming(document_id=ids[0], duration_ms=2.0, ready=True),
            IngestionTiming(document_id=ids[1], duration_ms=3.0, ready=not failed),
        ),
        runtime_failed=failed,
    )
    assert measured.recorded_documents == 2
    assert measured.index_build_ms is None
    assert measured.observed_ingestion_ms == 5.0
    assert not measured.coverage_complete


def test_reused_index_is_unknown_not_a_zero_build_time() -> None:
    from app.evaluation.costs import index_build_measurement

    measured = index_build_measurement((), reused=True)
    assert measured.reused
    assert measured.index_build_ms is None
    assert measured.observed_ingestion_ms is None
    assert not measured.coverage_complete


@pytest.mark.parametrize("duration", [-1.0, float("nan"), float("inf")])
def test_invalid_ingestion_clock_is_rejected(duration: float) -> None:
    from app.evaluation.costs import IngestionTiming

    with pytest.raises(ValueError):
        IngestionTiming(
            document_id="org-1:change-notice", duration_ms=duration, ready=True
        )


def test_duplicate_out_of_order_and_reused_measurements_are_rejected() -> None:
    from app.evaluation.costs import IngestionTiming, index_build_measurement

    first, second = generate_utility_corpus().documents[:2]
    event = IngestionTiming(document_id=first.id, duration_ms=1.0, ready=True)
    other = IngestionTiming(document_id=second.id, duration_ms=1.0, ready=True)
    for timings, reused in (
        ((event, event), False),
        ((other,), False),
        ((event,), True),
    ):
        with pytest.raises(ValueError):
            index_build_measurement(timings, reused=reused)


def test_storage_units_are_allocated_collection_kib_not_vector_geometry() -> None:
    from app.evaluation.costs import CollectionDiskMeasurement, storage_measurement

    measured = storage_measurement(
        database_bytes=32768,
        upload_bytes=500,
        collection_names=("ragelit_audit_cost",),
        disks=(CollectionDiskMeasurement(name="ragelit_audit_cost", allocated_kib=7),),
    )
    assert measured.database_bytes == 32768
    assert measured.upload_bytes == 500
    assert measured.qdrant_collection_disk_bytes == 7168
    assert measured.collection_count == 1
    assert measured.coverage_complete


def test_unavailable_vector_storage_stays_unknown_not_zero() -> None:
    from app.evaluation.costs import storage_measurement

    measured = storage_measurement(
        database_bytes=32768,
        upload_bytes=500,
        collection_names=("ragelit_audit_cost",),
        disks=(),
    )
    assert measured.database_bytes == 32768
    assert measured.qdrant_collection_disk_bytes is None
    assert not measured.coverage_complete


def test_foreign_collection_storage_cannot_enter_owned_total() -> None:
    from app.evaluation.costs import CollectionDiskMeasurement, storage_measurement

    with pytest.raises(ValueError):
        storage_measurement(
            database_bytes=32768,
            upload_bytes=500,
            collection_names=("ragelit_audit_cost",),
            disks=(
                CollectionDiskMeasurement(name="ragelit_audit_other", allocated_kib=7),
            ),
        )


@pytest.mark.parametrize("port", ["6333", "9999"])
def test_disk_probe_checks_service_identity_and_uses_exact_argv(
    port: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.evaluation import costs

    calls: list[list[str]] = []

    def run(arguments: list[str], **kwargs: Any) -> CompletedProcess[str]:
        calls.append(arguments)
        assert kwargs["check"] is True
        assert kwargs["timeout"] == 10
        if arguments[1] == "inspect":
            assert arguments[-1] == "owned-qdrant"
            return CompletedProcess(
                arguments,
                0,
                json.dumps(
                    {
                        "id": "a" * 64,
                        "image": "qdrant/qdrant:v1.15.4",
                        "ports": {
                            "6333/tcp": [{"HostIp": "127.0.0.1", "HostPort": port}]
                        },
                    }
                ),
                "",
            )
        assert arguments == [
            "docker",
            "exec",
            "a" * 64,
            "du",
            "-sk",
            "--",
            "/qdrant/storage/collections/ragelit_audit_cost",
        ]
        return CompletedProcess(
            arguments, 0, "7\t/qdrant/storage/collections/ragelit_audit_cost\n", ""
        )

    monkeypatch.setattr("app.evaluation.costs.subprocess.run", run)
    workspace = cast(
        AuditWorkspace,
        SimpleNamespace(
            validate_owned=lambda: None,
            config=SimpleNamespace(
                qdrant_url="http://127.0.0.1:6333",
                name="ragelit_audit_cost",
                vector_strategy="shared_pre_filter",
            ),
            store=SimpleNamespace(
                collection_names=lambda: ("ragelit_audit_cost",),
                client=SimpleNamespace(info=lambda: SimpleNamespace(version="1.15.4")),
            ),
        ),
    )
    if port == "9999":
        with pytest.raises(ValueError):
            costs.read_collection_disks(workspace, container="owned-qdrant")
        assert len(calls) == 1
    else:
        result = costs.read_collection_disks(workspace, container="owned-qdrant")
        assert result[0].allocated_kib == 7
        assert len(calls) == 2
