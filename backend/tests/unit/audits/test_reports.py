import hashlib
import json
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.audits.contracts import Boundary, BoundaryEvidence, RunMetadata
from app.audits.reports import build_report, write_report
from app.audits.scoring import score_case
from tests.unit.audits.support import case, metadata, observation


def test_report_contains_typed_metadata_and_no_raw_fields(tmp_path: Path) -> None:
    report = build_report((case(),), (score_case(case(), observation()),), metadata())
    path = write_report(report, tmp_path)
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert parsed["exit_code"] == 0
    assert parsed["metadata"]["profile"] == "safe"
    assert parsed["results"][0]["status"] == "pass"
    assert parsed["results"][0]["observation"]["http_status"] == 200
    for key in ("answer", "question", "prompt", "password", "access_token", "text"):
        assert f'"{key}"' not in path.read_text(encoding="utf-8")
    receipt = json.loads(path.with_suffix(".sha256.json").read_text(encoding="utf-8"))
    assert receipt["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_report_writer_refuses_existing_run_without_changing_it(tmp_path: Path) -> None:
    report = build_report((case(),), (score_case(case(), observation()),), metadata())
    path = write_report(report, tmp_path)
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        write_report(report, tmp_path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("field", ["answer", "prompt", "password", "token", "text"])
def test_report_metadata_rejects_raw_fields(field: str) -> None:
    payload = metadata().model_dump()
    payload[field] = "SyntheticSecretCanaryNeverExport"
    with pytest.raises(ValidationError):
        RunMetadata.model_validate(payload)


@pytest.mark.parametrize("duration", [float("nan"), float("inf"), -1.0])
def test_nonfinite_or_negative_stage_timing_is_rejected(duration: float) -> None:
    with pytest.raises(ValidationError):
        BoundaryEvidence(boundary=Boundary.CONTEXT, sequence=0, duration_ms=duration)


def test_more_than_twenty_chunk_ids_are_rejected() -> None:
    with pytest.raises(ValidationError):
        BoundaryEvidence(
            boundary=Boundary.CONTEXT,
            sequence=0,
            chunk_ids=tuple(UUID(int=n) for n in range(21)),
        )
