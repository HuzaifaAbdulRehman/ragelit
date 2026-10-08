import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal, cast
from uuid import UUID, uuid4

from pydantic import Field

from app.audits.artifacts import _checked_path, _read
from app.audits.contracts import (
    AuditCaseResult,
    AuditModel,
    AuditObservation,
    Boundary,
    Checksum,
)
from app.audits.fixtures import generate_fixtures
from app.audits.isolation_reports import IsolationStrategy, _artifact_object
from app.audits.reports import _publish
from app.audits.scoring import score_case
from app.audits.workspace import FixtureBindings
from app.evaluation.access_registry import access_registry
from app.evaluation.reports import BenchmarkProvenance, _collections
from app.evaluation.security_metrics import ExposureRate, boundary_exposure_rate

_ERROR = "access_benchmark_artifact_invalid"
_MARKERS = (b"AUDITCANARY", b"FACTANSWER", b"Bearer ", b"Authorization")
_BOUNDARIES = (
    Boundary.RETRIEVAL_RAW,
    Boundary.RETRIEVAL_ACCEPTED,
    Boundary.CONTEXT,
    Boundary.OUTPUT_CANDIDATE,
    Boundary.OUTPUT_DELIVERED,
)


class AccessObservation(AuditModel):
    observation: AuditObservation
    provider_profile: Literal["fixture", "local", "controlled_citation"]


class AccessBenchmarkReport(AuditModel):
    schema_version: Literal["access-benchmark-1"] = "access-benchmark-1"
    run_id: UUID
    cohort_id: UUID
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    provenance: BenchmarkProvenance
    pack_id: Literal["access-control-v1"] = "access-control-v1"
    seed: Literal[20261002] = 20261002
    template_hash: Checksum
    inventory: FixtureBindings
    strategy: IsolationStrategy
    collection_names: tuple[Annotated[str, Field(max_length=100)], ...] = Field(
        min_length=1, max_length=3
    )
    records: tuple[AccessObservation, ...] = Field(max_length=51)
    results: tuple[AuditCaseResult, ...] = Field(max_length=51)
    rates: tuple[ExposureRate, ...] = Field(min_length=5, max_length=5)
    coverage_complete: bool = Field(strict=True)
    runtime_failed: bool = Field(strict=True)
    provisional: bool = Field(strict=True)
    exit_code: Literal[0, 1, 2]
    evidence_notice: Literal[
        "Owned synthetic access-control cases; exact marker matches only. "
        "Controlled citation cases use fixture generation, "
        "not the declared local model. "
        "Checksums verify integrity, not execution or loaded-model attestation."
    ] = (
        "Owned synthetic access-control cases; exact marker matches only. "
        "Controlled citation cases use fixture generation, "
        "not the declared local model. "
        "Checksums verify integrity, not execution or loaded-model attestation."
    )


def build_access_benchmark(
    provenance: BenchmarkProvenance,
    inventory: FixtureBindings,
    records: tuple[AccessObservation, ...],
    *,
    strategy: str,
    collection_names: tuple[str, ...],
    run_id: UUID,
    runtime_failed: bool = False,
    provisional: bool = False,
) -> AccessBenchmarkReport:
    provenance = BenchmarkProvenance.model_validate(provenance.model_dump())
    inventory = FixtureBindings.model_validate(inventory.model_dump())
    records = tuple(
        AccessObservation.model_validate(item.model_dump()) for item in records
    )
    if (
        strategy not in {"shared_pre_filter", "tenant_collections", "lab_post_filter"}
        or type(runtime_failed) is not bool
        or type(provisional) is not bool
    ):
        raise ValueError(_ERROR)
    selected = cast(IsolationStrategy, strategy)
    _collections(selected, collection_names)
    expected_collections = (
        tuple(
            sorted(
                f"{inventory.collection}_tenant_{identifier.hex}"
                for identifier in inventory.organizations.values()
            )
        )
        if selected == "tenant_collections"
        else (inventory.collection,)
    )
    if collection_names != expected_collections:
        raise ValueError(_ERROR)
    registry = access_registry(inventory, run_id)
    cases = {case.id: case for case in registry.cases}
    identifiers = tuple(record.observation.case_id for record in records)
    if len(set(identifiers)) != len(identifiers) or not set(identifiers).issubset(
        cases
    ):
        raise ValueError(_ERROR)
    for record in records:
        observation = record.observation
        expected_profile = (
            "controlled_citation"
            if cases[observation.case_id].citation_challenge is not None
            else provenance.generation.mode
        )
        if record.provider_profile != expected_profile or any(
            not set(stage.chunk_ids).issubset(registry.known_chunk_ids)
            or any(
                match.canary_id not in registry.known_canary_ids
                for match in stage.canary_matches
            )
            for stage in observation.boundaries
        ):
            raise ValueError(_ERROR)
    observations = tuple(record.observation for record in records)
    results = tuple(
        score_case(cases[observation.case_id], observation)
        for observation in observations
    )
    rates = tuple(
        boundary_exposure_rate(registry.cases, observations, boundary)
        for boundary in _BOUNDARIES
    )
    complete = bool(
        len(results) == len(registry.cases)
        and all(result.coverage_complete for result in results)
        and all(rate.primary_rate is not None for rate in rates)
        and not runtime_failed
        and not provisional
    )
    gate: Literal[0, 1, 2] = (
        2
        if not complete
        else 1
        if any(result.status == "fail" for result in results)
        or any(rate.observed_exposures for rate in rates)
        else 0
    )
    return AccessBenchmarkReport(
        run_id=uuid4() if provisional else run_id,
        cohort_id=run_id,
        provenance=provenance,
        template_hash=generate_fixtures().checksum,
        inventory=inventory,
        strategy=selected,
        collection_names=collection_names,
        records=records,
        results=results,
        rates=rates,
        coverage_complete=complete,
        runtime_failed=runtime_failed,
        provisional=provisional,
        exit_code=gate,
    )


def _checked_report(report: AccessBenchmarkReport) -> AccessBenchmarkReport:
    report = AccessBenchmarkReport.model_validate(report.model_dump())
    if (
        report.created_at.tzinfo is None
        or not report.provisional
        and report.run_id != report.cohort_id
    ):
        raise ValueError(_ERROR)
    replayed = build_access_benchmark(
        report.provenance,
        report.inventory,
        report.records,
        strategy=report.strategy,
        collection_names=report.collection_names,
        run_id=report.cohort_id,
        runtime_failed=report.runtime_failed,
        provisional=report.provisional,
    ).model_copy(update={"run_id": report.run_id, "created_at": report.created_at})
    if replayed != report:
        raise ValueError(_ERROR)
    return report


def _content(report: AccessBenchmarkReport) -> bytes:
    content = (_checked_report(report).model_dump_json(indent=2) + "\n").encode()
    if len(content) > 8 * 1024 * 1024 or any(marker in content for marker in _MARKERS):
        raise ValueError(_ERROR)
    return content


def write_access_benchmark(report: AccessBenchmarkReport, directory: Path) -> Path:
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
        raise FileExistsError("access benchmark artifact already exists")
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


def validate_access_benchmark(path: Path) -> AccessBenchmarkReport:
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
    report = AccessBenchmarkReport.model_validate_json(content)
    if report.run_id != run_id:
        raise ValueError(_ERROR)
    _content(report)
    return report
