from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.audits.reports import AuditReport

JobState = Literal["queued", "running", "finished", "recovery_required"]
JobOutcome = Literal["unknown", "pass", "fail", "inconclusive"]


class AuditRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuditRunSummary(BaseModel):
    id: UUID
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    state: JobState
    outcome: JobOutcome
    exit_code: Literal[0, 1, 2] | None
    error_code: str | None
    report_available: bool
    report_id: UUID | None
    report_sha256: str | None


class AuditRunList(BaseModel):
    items: list[AuditRunSummary]
    total: int
    limit: int
    offset: int


class AuditRunDetail(AuditRunSummary):
    report: AuditReport | None
