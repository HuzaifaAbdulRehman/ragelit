import hashlib
import json
import os
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field

from app.audits.contracts import (
    AuditCase,
    AuditCaseResult,
    AuditModel,
    Reason,
    RunMetadata,
)
from app.audits.scoring import score_case


class AuditReport(AuditModel):
    schema_version: Literal["1"] = "1"
    run_id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: RunMetadata
    required_case_ids: tuple[str, ...]
    results: tuple[AuditCaseResult, ...] = Field(max_length=500)
    coverage_complete: bool
    exit_code: Literal[0, 1, 2]
    inventory_reason: Reason | None = None
    runtime_failed: bool = False
    retrieval_notice: Literal[
        "Retrieval evidence covers returned fused results, not internal "
        "dense or sparse prefetch candidates."
    ] = (
        "Retrieval evidence covers returned fused results, not internal "
        "dense or sparse prefetch candidates."
    )
    scope_notice: Literal[
        "Synthetic fixtures and deterministic providers only; "
        "not a security certification or model-quality benchmark."
    ] = (
        "Synthetic fixtures and deterministic providers only; "
        "not a security certification or model-quality benchmark."
    )


def build_report(
    cases: tuple[AuditCase, ...],
    results: tuple[AuditCaseResult, ...],
    metadata: RunMetadata,
    *,
    runtime_failed: bool = False,
) -> AuditReport:
    expected = {case.id: case for case in cases}
    counts = Counter(result.case_id for result in results)
    inventory_complete = (
        bool(cases)
        and len(expected) == len(cases)
        and set(counts) == set(expected)
        and all(count == 1 for count in counts.values())
    )
    checked = tuple(
        score_case(expected[result.case_id], result.observation)
        if result.case_id in expected
        else result
        for result in results
    )
    complete = (
        not runtime_failed
        and inventory_complete
        and all(result.coverage_complete for result in checked)
    )
    exit_code: Literal[0, 1, 2] = (
        2
        if not complete
        else 1
        if any(result.status == "fail" for result in checked)
        else 0
    )
    return AuditReport(
        metadata=metadata,
        required_case_ids=tuple(case.id for case in cases),
        results=checked,
        coverage_complete=complete,
        exit_code=exit_code,
        inventory_reason=None if inventory_complete else Reason.INVENTORY_INCOMPLETE,
        runtime_failed=runtime_failed,
    )


def _publish(destination: Path, content: bytes) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=".audit-", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_report(report: AuditReport, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{report.run_id}.json"
    receipt = destination.with_suffix(".sha256.json")
    if destination.exists() or receipt.exists():
        raise FileExistsError("audit artifact already exists")
    content = (report.model_dump_json(indent=2) + "\n").encode("utf-8")
    receipt_content = (
        json.dumps(
            {
                "filename": destination.name,
                "sha256": hashlib.sha256(content).hexdigest(),
            },
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    _publish(receipt, receipt_content)
    _publish(destination, content)
    return destination
