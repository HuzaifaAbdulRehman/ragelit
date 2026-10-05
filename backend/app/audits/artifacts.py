import hashlib
import json
import re
from pathlib import Path
from uuid import UUID

from app.audits.contracts import Reason
from app.audits.embeddings import FixtureEmbeddings
from app.audits.fixtures import generate_fixtures
from app.audits.reports import AuditReport
from app.audits.seeding import FixtureCitingProvider


def _checked_path(path: Path) -> Path:
    absolute = path.absolute()
    if any(
        part.is_symlink() or part.is_junction()
        for part in (absolute, *absolute.parents)
    ):
        raise ValueError("audit_artifact_invalid")
    return absolute.resolve(strict=True)


def _read(path: Path, limit: int) -> bytes:
    with _checked_path(path).open("rb") as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise ValueError("audit_artifact_invalid")
    return content


def validate_report(path: Path) -> AuditReport:
    path = _checked_path(path)
    run_id = UUID(path.stem)
    if path.name != f"{run_id}.json":
        raise ValueError("audit_artifact_invalid")
    content = _read(path, 8 * 1024 * 1024)
    receipt = json.loads(_read(path.with_suffix(".sha256.json"), 4096))
    if receipt != {
        "filename": path.name,
        "sha256": hashlib.sha256(content).hexdigest(),
    }:
        raise ValueError("audit_artifact_invalid")
    report = AuditReport.model_validate_json(content)
    template = generate_fixtures()
    expected = tuple(control.id for control in template.cases)
    if (
        report.run_id != run_id
        or report.required_case_ids != expected
        or report.metadata.pack_id != template.pack_id
        or report.metadata.generator_id != template.generator_id
        or report.metadata.embedding_id != FixtureEmbeddings.identifier
        or report.metadata.provider_id != FixtureCitingProvider.identifier
        or report.metadata.template_hash != template.checksum
        or any(marker in content for marker in (b"AUDITCANARY", b"Bearer "))
    ):
        raise ValueError("audit_artifact_invalid")
    seen = [result.case_id for result in report.results]
    if len(set(seen)) != len(seen) or not set(seen).issubset(expected):
        raise ValueError("audit_artifact_invalid")
    inventory_complete = set(seen) == set(expected)
    complete = (
        inventory_complete
        and not report.runtime_failed
        and all(result.coverage_complete for result in report.results)
    )
    gate = (
        2
        if not complete
        else 1
        if any(result.status == "fail" for result in report.results)
        else 0
    )
    if (
        report.coverage_complete != complete
        or report.exit_code != gate
        or report.inventory_reason
        != (None if inventory_complete else Reason.INVENTORY_INCOMPLETE)
        or complete
        and any(result.status == "inconclusive" for result in report.results)
    ):
        raise ValueError("audit_artifact_invalid")
    for result in report.results:
        if result.observation.case_id != result.case_id:
            raise ValueError("audit_artifact_invalid")
        for stage in result.observation.boundaries:
            for match in stage.canary_matches:
                if match.canary_id not in template.canaries and not re.fullmatch(
                    r"instance-[0-9a-f]{32}", match.canary_id
                ):
                    raise ValueError("audit_artifact_invalid")
    return report


def validate_release_directory(directory: Path) -> tuple[AuditReport, ...]:
    directory = _checked_path(directory)
    entries = tuple(directory.iterdir())
    reports = tuple(
        validate_report(path)
        for path in entries
        if path.suffix == ".json" and not path.name.endswith(".sha256.json")
    )
    expected_files = {
        f"{report.run_id}{suffix}"
        for report in reports
        for suffix in (".json", ".sha256.json")
    }
    if {entry.name for entry in entries} != expected_files or len(reports) != 3:
        raise ValueError("audit_artifact_invalid")
    profiles: dict[str, AuditReport] = {
        report.metadata.profile: report for report in reports
    }
    if set(profiles) != {"safe", "vulnerable", "deny_all"}:
        raise ValueError("audit_artifact_invalid")
    if any(
        not report.coverage_complete or report.exit_code != expected
        for profile, expected in (("safe", 0), ("vulnerable", 1), ("deny_all", 1))
        for report in (profiles[profile],)
    ):
        raise ValueError("audit_artifact_invalid")
    return reports
