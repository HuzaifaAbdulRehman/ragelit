import hashlib
import json
import re
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from app.audits.artifacts import _checked_path, _read
from app.audits.contracts import AuditCase, AuditModel, Boundary, Terminal
from app.audits.embeddings import FixtureEmbeddings
from app.audits.fixtures import generate_fixtures
from app.audits.reports import AuditReport, _publish, build_report
from app.audits.seeding import FixtureCitingProvider

IsolationStrategy = Literal[
    "shared_pre_filter", "tenant_collections", "lab_post_filter"
]
_NAMESPACE = r"ragelit_audit_[a-z0-9_]{1,32}"
_ERROR = "isolation_artifact_invalid"
_MARKERS = (b"AUDITCANARY", b"Bearer ")


class IsolationReport(AuditModel):
    schema_version: Literal["isolation-1"] = "isolation-1"
    strategy: IsolationStrategy
    collections: tuple[Annotated[str, Field(max_length=100)], ...] = Field(
        min_length=1, max_length=3
    )
    cases: tuple[AuditCase, ...] = Field(min_length=51, max_length=51)
    audit: AuditReport


def _checked_report(report: IsolationReport) -> IsolationReport:
    report = IsolationReport.model_validate(report.model_dump())
    template = generate_fixtures()
    audit = report.audit
    expected = tuple(logical.id for logical in template.cases)
    if (
        audit.metadata.profile != "safe"
        or audit.metadata.pack_id != template.pack_id
        or audit.metadata.generator_id != template.generator_id
        or audit.metadata.embedding_id != FixtureEmbeddings.identifier
        or audit.metadata.provider_id != FixtureCitingProvider.identifier
        or audit.metadata.template_hash != template.checksum
        or audit.required_case_ids != expected
        or tuple(case.id for case in report.cases) != expected
    ):
        raise ValueError(_ERROR)
    names = report.collections
    if report.strategy == "tenant_collections":
        parsed = [
            re.fullmatch(rf"({_NAMESPACE})_tenant_([0-9a-f]{{32}})", name)
            for name in names
        ]
        if (
            len(names) != 3
            or len(set(names)) != 3
            or names != tuple(sorted(names))
            or any(match is None for match in parsed)
            or len({match.group(1) for match in parsed if match is not None}) != 1
        ):
            raise ValueError(_ERROR)
    elif len(names) != 1 or re.fullmatch(_NAMESPACE, names[0]) is None:
        raise ValueError(_ERROR)
    for logical, case in zip(template.cases, report.cases, strict=True):
        if (
            case.positive != logical.positive
            or case.expected_status != logical.expected_status
            or case.expected_denial_code
            != (
                "membership_inactive" if logical.action == "revoke_membership" else None
            )
            or len(case.required_chunks) != (1 if logical.positive else 0)
            or not case.forbidden_chunks
            or not case.forbidden_canaries
            or case.required_boundaries != tuple(Boundary)
            or (case.citation_challenge is not None)
            != (logical.action == "challenge_citation")
            or case.citation_challenge is not None
            and case.citation_challenge not in case.forbidden_chunks
        ):
            raise ValueError(_ERROR)
    identifiers = tuple(result.case_id for result in audit.results)
    if len(set(identifiers)) != len(identifiers) or not set(identifiers).issubset(
        expected
    ):
        raise ValueError(_ERROR)
    replayed = build_report(
        report.cases, audit.results, audit.metadata, runtime_failed=audit.runtime_failed
    ).model_copy(update={"run_id": audit.run_id, "created_at": audit.created_at})
    if replayed != audit:
        raise ValueError(_ERROR)
    return report


def _content(report: IsolationReport) -> bytes:
    checked = _checked_report(report)
    content = (checked.model_dump_json(indent=2) + "\n").encode()
    if len(content) > 8 * 1024 * 1024 or any(marker in content for marker in _MARKERS):
        raise ValueError(_ERROR)
    return content


def write_isolation_report(report: IsolationReport, directory: Path) -> Path:
    content = _content(report)
    directory = directory.absolute()
    if any(
        part.is_symlink() or part.is_junction()
        for part in (directory, *directory.parents)
    ):
        raise ValueError(_ERROR)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{report.audit.run_id}.json"
    receipt = destination.with_suffix(".sha256.json")
    if any(path.exists() or path.is_symlink() for path in (destination, receipt)):
        raise FileExistsError("isolation artifact already exists")
    receipt_content = (
        json.dumps(
            {
                "filename": destination.name,
                "sha256": hashlib.sha256(content).hexdigest(),
            },
            sort_keys=True,
        )
        + "\n"
    ).encode()
    _publish(receipt, receipt_content)
    _publish(destination, content)
    return destination


def _artifact_object(content: bytes) -> dict[str, object]:
    if any(marker in content for marker in _MARKERS):
        raise ValueError(_ERROR)

    def unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
        fields: dict[str, object] = {}
        for name, value in pairs:
            if name in fields:
                raise ValueError(_ERROR)
            fields[name] = value
        return fields

    parsed: object = json.loads(content, object_pairs_hook=unique_fields)
    if not isinstance(parsed, dict):
        raise ValueError(_ERROR)
    return parsed


def validate_isolation_report(path: Path) -> IsolationReport:
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
    report = IsolationReport.model_validate_json(content)
    if report.audit.run_id != run_id:
        raise ValueError(_ERROR)
    _content(report)
    return report


def validate_isolation_release_directory(
    directory: Path,
) -> tuple[IsolationReport, ...]:
    directory = _checked_path(directory)
    entries = tuple(directory.iterdir())
    if len(entries) != 6:
        raise ValueError(_ERROR)
    reports = tuple(
        validate_isolation_report(path)
        for path in entries
        if path.suffix == ".json" and not path.name.endswith(".sha256.json")
    )
    expected_files = {
        f"{report.audit.run_id}{suffix}"
        for report in reports
        for suffix in (".json", ".sha256.json")
    }
    strategies = {report.strategy: report for report in reports}
    if (
        {entry.name for entry in entries} != expected_files
        or len(reports) != 3
        or set(strategies)
        != {"shared_pre_filter", "tenant_collections", "lab_post_filter"}
        or len(
            {
                (
                    report.audit.metadata.git_revision,
                    report.audit.metadata.git_dirty,
                    report.audit.metadata.lock_hashes,
                )
                for report in reports
            }
        )
        != 1
    ):
        raise ValueError(_ERROR)
    for strategy, report in strategies.items():
        audit = report.audit
        if not audit.coverage_complete or audit.exit_code != (
            1 if strategy == "lab_post_filter" else 0
        ):
            raise ValueError(_ERROR)
        if strategy != "lab_post_filter":
            continue
        if not any(
            result.first_exposure == Boundary.RETRIEVAL_RAW for result in audit.results
        ):
            raise ValueError(_ERROR)
        cases = {case.id: case for case in report.cases}
        for result in audit.results:
            case = cases[result.case_id]
            for stage in result.observation.boundaries:
                if stage.boundary == Boundary.RETRIEVAL_RAW:
                    continue
                forbidden = set(case.forbidden_chunks)
                if (
                    stage.boundary == Boundary.CITATIONS_CANDIDATE
                    and result.observation.terminal == Terminal.CITATIONS_REJECTED
                    and result.observation.http_status == 502
                ):
                    forbidden.discard(case.citation_challenge)
                if set(stage.chunk_ids) & forbidden or any(
                    match.canary_id in case.forbidden_canaries
                    for match in stage.canary_matches
                ):
                    raise ValueError(_ERROR)
    return reports
