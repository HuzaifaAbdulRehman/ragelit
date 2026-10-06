import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal, cast
from uuid import UUID, uuid4

from pydantic import Field

from app.audits.artifacts import _checked_path, _read
from app.audits.contracts import (
    AuditModel,
    AuditObservation,
    Boundary,
    Checksum,
    Identifier,
)
from app.audits.injection_fixtures import fact_marker, generate_injection_fixtures
from app.audits.injection_reports import expected_injection_cases
from app.audits.isolation_reports import IsolationStrategy, _artifact_object
from app.audits.reports import _publish
from app.audits.workspace import DocumentBinding
from app.evaluation.reports import BenchmarkProvenance, _collections
from app.evaluation.security_metrics import (
    ExposureRate,
    InjectionRate,
    boundary_exposure_rate,
    injection_success_rate,
)

_ERROR = "injection_benchmark_artifact_invalid"
_MARKERS = (b"AUDITCANARY", b"FACTANSWER", b"Bearer ", b"Authorization")
Profile = Literal["resistant", "obeying", "deny_all", "local"]
_BOUNDARIES = (
    Boundary.RETRIEVAL_RAW,
    Boundary.RETRIEVAL_ACCEPTED,
    Boundary.CONTEXT,
    Boundary.OUTPUT_CANDIDATE,
    Boundary.OUTPUT_DELIVERED,
)


class InjectionBenchmarkReport(AuditModel):
    schema_version: Literal["injection-benchmark-1"] = "injection-benchmark-1"
    run_id: UUID
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    provenance: BenchmarkProvenance
    pack_id: Literal["injection-v1"] = "injection-v1"
    seed: Literal[20261002] = 20261002
    template_hash: Checksum
    trials: int = Field(strict=True, ge=1, le=20)
    provider_profile: Profile
    strategy: IsolationStrategy
    collection_names: tuple[Annotated[str, Field(max_length=100)], ...] = Field(
        min_length=1, max_length=3
    )
    documents: dict[Identifier, DocumentBinding] = Field(min_length=6, max_length=6)
    observations: tuple[AuditObservation, ...] = Field(max_length=120)
    rates: tuple[ExposureRate, ...] = Field(min_length=5, max_length=5)
    injection: InjectionRate
    coverage_complete: bool = Field(strict=True)
    runtime_failed: bool = Field(strict=True)
    provisional: bool = Field(strict=True)
    exit_code: Literal[0, 1, 2]
    evidence_notice: Literal[
        "Owned synthetic injection cases; exact marker matches only. "
        "Fixture profiles test the harness, not model resistance. "
        "Checksums verify integrity, not execution or loaded-model attestation."
    ] = (
        "Owned synthetic injection cases; exact marker matches only. "
        "Fixture profiles test the harness, not model resistance. "
        "Checksums verify integrity, not execution or loaded-model attestation."
    )


def build_injection_benchmark(
    provenance: BenchmarkProvenance,
    documents: dict[str, DocumentBinding],
    observations: tuple[AuditObservation, ...],
    *,
    strategy: str,
    collection_names: tuple[str, ...],
    provider_profile: str,
    trials: int = 1,
    runtime_failed: bool = False,
    provisional: bool = False,
    run_id: UUID | None = None,
) -> InjectionBenchmarkReport:
    provenance = BenchmarkProvenance.model_validate(provenance.model_dump())
    documents = {
        name: DocumentBinding.model_validate(binding.model_dump())
        for name, binding in documents.items()
    }
    observations = tuple(
        AuditObservation.model_validate(item.model_dump()) for item in observations
    )
    if (
        strategy not in {"shared_pre_filter", "tenant_collections", "lab_post_filter"}
        or provider_profile not in {"resistant", "obeying", "deny_all", "local"}
        or (provider_profile == "local") != (provenance.generation.mode == "local")
        or type(runtime_failed) is not bool
        or type(provisional) is not bool
    ):
        raise ValueError(_ERROR)
    selected = cast(IsolationStrategy, strategy)
    _collections(selected, collection_names)
    template = generate_injection_fixtures(trials=trials)
    controls = expected_injection_cases(documents, trials=trials)
    known_chunks = {
        chunk for binding in documents.values() for chunk in binding.chunk_ids
    }
    known_canaries = set(template.canaries) | {
        fact_marker(template.seed, doc.id)[0] for doc in template.documents
    }
    if any(
        not set(stage.chunk_ids).issubset(known_chunks)
        or any(match.canary_id not in known_canaries for match in stage.canary_matches)
        for observation in observations
        for stage in observation.boundaries
    ):
        raise ValueError(_ERROR)
    access_cases = tuple(control.access_case for control in controls)
    rates = tuple(
        boundary_exposure_rate(access_cases, observations, boundary)
        for boundary in _BOUNDARIES
    )
    injection = injection_success_rate(
        controls, observations, runtime_failed=runtime_failed
    )
    complete = bool(
        injection.summary.coverage_complete
        and injection.primary_rate is not None
        and all(rate.primary_rate is not None for rate in rates)
        and not provisional
        and not runtime_failed
    )
    gate: Literal[0, 1, 2] = (
        2
        if not complete
        else 1
        if injection.summary.exit_code == 1
        or any(rate.observed_exposures for rate in rates)
        else 0
    )
    return InjectionBenchmarkReport(
        run_id=run_id or uuid4(),
        provenance=provenance,
        template_hash=template.checksum,
        trials=trials,
        provider_profile=cast(Profile, provider_profile),
        strategy=selected,
        collection_names=collection_names,
        documents=documents,
        observations=observations,
        rates=rates,
        injection=injection,
        coverage_complete=complete,
        runtime_failed=runtime_failed,
        provisional=provisional,
        exit_code=gate,
    )


def _checked_report(report: InjectionBenchmarkReport) -> InjectionBenchmarkReport:
    report = InjectionBenchmarkReport.model_validate(report.model_dump())
    replayed = build_injection_benchmark(
        report.provenance,
        report.documents,
        report.observations,
        strategy=report.strategy,
        collection_names=report.collection_names,
        provider_profile=report.provider_profile,
        trials=report.trials,
        runtime_failed=report.runtime_failed,
        provisional=report.provisional,
        run_id=report.run_id,
    ).model_copy(update={"created_at": report.created_at})
    if replayed != report or report.created_at.tzinfo is None:
        raise ValueError(_ERROR)
    return report


def _content(report: InjectionBenchmarkReport) -> bytes:
    content = (_checked_report(report).model_dump_json(indent=2) + "\n").encode()
    if len(content) > 8 * 1024 * 1024 or any(marker in content for marker in _MARKERS):
        raise ValueError(_ERROR)
    return content


def write_injection_benchmark(
    report: InjectionBenchmarkReport, directory: Path
) -> Path:
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
        raise FileExistsError("injection benchmark artifact already exists")
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


def validate_injection_benchmark(path: Path) -> InjectionBenchmarkReport:
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
    report = InjectionBenchmarkReport.model_validate_json(content)
    if report.run_id != run_id:
        raise ValueError(_ERROR)
    _content(report)
    return report
