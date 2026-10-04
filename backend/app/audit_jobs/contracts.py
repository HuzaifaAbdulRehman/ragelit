from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID


@dataclass(frozen=True)
class AuditClaim:
    request_id: UUID
    organization_id: UUID
    requester_user_id: UUID
    claim_id: UUID
    lease_until: datetime


@dataclass(frozen=True)
class CliOutcome:
    exit_code: Literal[0, 1, 2]
    report_content: str | None = None
    report_id: UUID | None = None
    report_sha256: str | None = None
    error_code: str | None = None
    recovery_required: bool = False
