import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean
from typing import Annotated, Literal, cast
from uuid import UUID, uuid4

from pydantic import Field

from app.audits.artifacts import _checked_path, _read
from app.audits.contracts import AuditModel
from app.audits.isolation_reports import IsolationStrategy, _artifact_object
from app.audits.reports import _publish
from app.evaluation.costs import IndexBuildMeasurement, StorageMeasurement
from app.evaluation.dataset import generate_utility_corpus
from app.evaluation.reports import (
    BenchmarkProvenance,
    UtilityReport,
    _bindings,
    _collections,
    _result,
)
from app.evaluation.reports import (
    _checked_report as checked_utility,
)
from app.evaluation.revocations import RevocationMeasurement

_ERROR = "utility_cost_artifact_invalid"
_MARKERS = (b"AUDITCANARY", b"FACTANSWER", b"Bearer ", b"Authorization")
Duration = Annotated[float, Field(strict=True, ge=0)]
Bytes = Annotated[int, Field(strict=True, ge=0)]


class CostSummary(AuditModel):
    index_expected: Literal[87] = 87
    index_recorded: int = Field(strict=True, ge=0, le=87)
    index_build_ms: Duration | None
    observed_ingestion_ms: Duration | None
    index_reused: bool | None = Field(strict=True)
    database_bytes: Bytes | None
    upload_bytes: Bytes | None
    qdrant_collection_disk_bytes: Bytes | None
    collection_count: int = Field(strict=True, ge=1, le=3)
    revocation_expected: Literal[3] = 3
    revocation_recorded: int = Field(strict=True, ge=0, le=3)
    revocation_measured: int = Field(strict=True, ge=0, le=3)
    revocation_mean_ms: Duration | None
    observed_revocation_mean_ms: Duration | None


class UtilityCostReport(AuditModel):
    schema_version: Literal["utility-cost-1"] = "utility-cost-1"
    run_id: UUID
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    provenance: BenchmarkProvenance
    strategy: IsolationStrategy
    collection_names: tuple[Annotated[str, Field(max_length=100)], ...] = Field(
        min_length=1, max_length=3
    )
    utility: UtilityReport | None = None
    index: IndexBuildMeasurement | None = None
    storage: StorageMeasurement | None = None
    revocations: tuple[RevocationMeasurement, ...] = Field(default=(), max_length=3)
    summary: CostSummary
    costs_complete: bool = Field(strict=True)
    coverage_complete: bool = Field(strict=True)
    runtime_failed: bool = Field(strict=True)
    provisional: bool = Field(strict=True)
    exit_code: Literal[0, 2]
    evidence_notice: Literal[
        "Utility and cost evidence only; security results are still required. "
        "Checksums verify integrity, not execution or loaded-model attestation."
    ] = (
        "Utility and cost evidence only; security results are still required. "
        "Checksums verify integrity, not execution or loaded-model attestation."
    )


def build_cost_report(
    provenance: BenchmarkProvenance,
    *,
    strategy: str,
    collection_names: tuple[str, ...],
    utility: UtilityReport | None = None,
    index: IndexBuildMeasurement | None = None,
    storage: StorageMeasurement | None = None,
    revocations: tuple[RevocationMeasurement, ...] = (),
    runtime_failed: bool = False,
    provisional: bool = False,
    run_id: UUID | None = None,
) -> UtilityCostReport:
    provenance = BenchmarkProvenance.model_validate(provenance.model_dump())
    if strategy not in {"shared_pre_filter", "tenant_collections", "lab_post_filter"}:
        raise ValueError(_ERROR)
    selected = cast(IsolationStrategy, strategy)
    _collections(selected, collection_names)
    utility = checked_utility(utility) if utility is not None else None
    index = (
        IndexBuildMeasurement.model_validate(index.model_dump())
        if index is not None
        else None
    )
    storage = (
        StorageMeasurement.model_validate(storage.model_dump())
        if storage is not None
        else None
    )
    revocations = tuple(
        RevocationMeasurement.model_validate(event.model_dump())
        for event in revocations
    )
    if (
        utility is not None
        and (
            utility.provenance != provenance
            or utility.strategy != selected
            or utility.collection_names != collection_names
        )
        or storage is not None
        and storage.collection_names != collection_names
        or utility is None
        and revocations
    ):
        raise ValueError(_ERROR)
    corpus = generate_utility_corpus()
    expected = tuple(f"{org.id}-change-notice" for org in corpus.organizations)
    if (
        tuple(event.target.logical_id for event in revocations)
        != expected[: len(revocations)]
    ):
        raise ValueError(_ERROR)
    if utility is not None:
        bound = {item.logical_id: item for item in utility.bindings}
        by_chunk = _bindings(corpus, utility.bindings)
        for event in revocations:
            if event.target != bound[event.target.logical_id]:
                raise ValueError(_ERROR)
            for record in (event.before, event.after):
                if record is not None:
                    _result(record, corpus, utility.bindings, by_chunk)
    observed = tuple(
        event.revocation_to_confirmation_ms
        for event in revocations
        if event.revocation_to_confirmation_ms is not None
    )
    costs_complete = bool(
        index is not None
        and index.coverage_complete
        and storage is not None
        and storage.coverage_complete
        and len(observed) == 3
        and not runtime_failed
    )
    complete = bool(
        costs_complete
        and utility is not None
        and utility.coverage_complete
        and not provisional
    )
    return UtilityCostReport(
        run_id=run_id or (utility.run_id if utility is not None else uuid4()),
        provenance=provenance,
        strategy=selected,
        collection_names=collection_names,
        utility=utility,
        index=index,
        storage=storage,
        revocations=revocations,
        summary=CostSummary(
            index_recorded=index.recorded_documents if index is not None else 0,
            index_build_ms=index.index_build_ms if index is not None else None,
            observed_ingestion_ms=index.observed_ingestion_ms
            if index is not None
            else None,
            index_reused=index.reused if index is not None else None,
            database_bytes=storage.database_bytes if storage is not None else None,
            upload_bytes=storage.upload_bytes if storage is not None else None,
            qdrant_collection_disk_bytes=storage.qdrant_collection_disk_bytes
            if storage is not None
            else None,
            collection_count=len(collection_names),
            revocation_recorded=len(revocations),
            revocation_measured=len(observed),
            revocation_mean_ms=fmean(observed) if len(observed) == 3 else None,
            observed_revocation_mean_ms=fmean(observed) if observed else None,
        ),
        costs_complete=costs_complete,
        coverage_complete=complete,
        runtime_failed=runtime_failed,
        provisional=provisional,
        exit_code=0 if complete else 2,
    )


def _checked_report(report: UtilityCostReport) -> UtilityCostReport:
    report = UtilityCostReport.model_validate(report.model_dump())
    replayed = build_cost_report(
        report.provenance,
        strategy=report.strategy,
        collection_names=report.collection_names,
        utility=report.utility,
        index=report.index,
        storage=report.storage,
        revocations=report.revocations,
        runtime_failed=report.runtime_failed,
        provisional=report.provisional,
        run_id=report.run_id,
    ).model_copy(update={"created_at": report.created_at})
    if replayed != report or report.created_at.tzinfo is None:
        raise ValueError(_ERROR)
    return report


def _content(report: UtilityCostReport) -> bytes:
    content = (_checked_report(report).model_dump_json(indent=2) + "\n").encode()
    if len(content) > 8 * 1024 * 1024 or any(marker in content for marker in _MARKERS):
        raise ValueError(_ERROR)
    return content


def write_cost_report(report: UtilityCostReport, directory: Path) -> Path:
    content = _content(report)
    directory = directory.absolute()
    if ".." in directory.parts or any(
        part.is_symlink() or part.is_junction()
        for part in (directory, *directory.parents)
    ):
        raise ValueError(_ERROR)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{report.run_id}.json"
    receipt = destination.with_suffix(".sha256.json")
    if any(path.exists() or path.is_symlink() for path in (destination, receipt)):
        raise FileExistsError("utility cost artifact already exists")
    _publish(
        receipt,
        (
            json.dumps(
                {
                    "filename": destination.name,
                    "sha256": hashlib.sha256(content).hexdigest(),
                },
                sort_keys=True,
            )
            + "\n"
        ).encode(),
    )
    _publish(destination, content)
    return destination


def validate_cost_report(path: Path) -> UtilityCostReport:
    path = _checked_path(path)
    run_id = UUID(path.stem)
    if path.name != f"{run_id}.json":
        raise ValueError(_ERROR)
    content = _read(path, 8 * 1024 * 1024)
    receipt = _artifact_object(_read(path.with_suffix(".sha256.json"), 4096))
    if receipt != {
        "filename": path.name,
        "sha256": hashlib.sha256(content).hexdigest(),
    }:
        raise ValueError(_ERROR)
    _artifact_object(content)
    report = UtilityCostReport.model_validate_json(content)
    if report.run_id != run_id:
        raise ValueError(_ERROR)
    _content(report)
    return report
