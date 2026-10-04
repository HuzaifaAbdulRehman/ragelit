import hashlib
import json
from pathlib import Path

import pytest

from app.audits.contracts import AuditCase
from app.audits.fixtures import generate_fixtures
from app.audits.reports import build_report, write_report
from tests.unit.audits.support import metadata


def partial_artifact(directory: Path, profile: str = "safe") -> Path:
    template = generate_fixtures()
    controls = tuple(
        AuditCase(id=control.id, expected_status=control.expected_status)
        for control in template.cases
    )
    facts = metadata().model_copy(
        update={
            "profile": profile,
            "pack_id": "access-control-v1",
            "generator_id": "synthetic-fixtures-v1",
            "embedding_id": "fixture-topic-v1",
            "provider_id": "fixture-citing-v1",
            "template_hash": template.checksum,
        }
    )
    return write_report(build_report(controls, (), facts), directory)


def rewrite_with_receipt(path: Path, payload: dict[str, object]) -> None:
    content = json.dumps(payload).encode()
    path.write_bytes(content)
    path.with_suffix(".sha256.json").write_text(
        json.dumps(
            {"filename": path.name, "sha256": hashlib.sha256(content).hexdigest()}
        ),
        encoding="utf-8",
    )


def test_valid_partial_artifact_stays_incomplete(tmp_path: Path) -> None:
    from app.audits.artifacts import validate_report

    report = validate_report(partial_artifact(tmp_path))
    assert report.exit_code == 2
    assert not report.coverage_complete
    assert len(report.required_case_ids) == 51


def test_artifact_content_must_match_its_receipt(tmp_path: Path) -> None:
    from app.audits.artifacts import validate_report

    path = partial_artifact(tmp_path)
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError):
        validate_report(path)


@pytest.mark.parametrize(
    "change", ["raw_field", "metadata", "inventory", "false_pass", "receipt", "run_id"]
)
def test_artifact_validator_rejects_unsafe_or_inconsistent_payloads(
    tmp_path: Path, change: str
) -> None:
    from app.audits.artifacts import validate_report

    path = partial_artifact(tmp_path)
    payload = json.loads(path.read_bytes())
    if change == "raw_field":
        payload["answer"] = "AUDITCANARYSyntheticSensitiveArtifact"
    elif change == "metadata":
        payload["metadata"]["provider_id"] = "SyntheticSensitiveArtifact"
    elif change == "inventory":
        payload["required_case_ids"] = ["org-1:organization"]
    elif change == "false_pass":
        payload["exit_code"] = 0
        payload["coverage_complete"] = True
    elif change == "run_id":
        payload["run_id"] = "00000000-0000-0000-0000-000000000000"
    rewrite_with_receipt(path, payload)
    if change == "receipt":
        path.with_suffix(".sha256.json").write_text(
            json.dumps(
                {
                    "filename": "unexpected.json",
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            ),
            encoding="utf-8",
        )
    with pytest.raises(ValueError):
        validate_report(path)


def test_release_artifact_gate_rejects_a_partial_pack(tmp_path: Path) -> None:
    from app.audits.artifacts import validate_release_directory

    for profile in ("safe", "vulnerable", "deny_all"):
        partial_artifact(tmp_path, profile)
    with pytest.raises(ValueError):
        validate_release_directory(tmp_path)
