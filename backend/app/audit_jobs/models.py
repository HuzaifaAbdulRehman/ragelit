from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AuditRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "audit_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["requester_user_id", "organization_id"],
            ["memberships.user_id", "memberships.organization_id"],
            name="fk_audit_runs_membership",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "state IN ('queued','running','finished','recovery_required')", name="state"
        ),
        CheckConstraint(
            "outcome IN ('unknown','pass','fail','inconclusive')", name="outcome"
        ),
        CheckConstraint(
            "(state IN ('queued','running') AND outcome = 'unknown' "
            "AND exit_code IS NULL AND finished_at IS NULL) OR "
            "(state IN ('finished','recovery_required') AND finished_at IS NOT NULL "
            "AND exit_code IS NOT NULL AND ((outcome = 'pass' AND exit_code = 0) OR "
            "(outcome = 'fail' AND exit_code = 1) OR "
            "(outcome = 'inconclusive' AND exit_code = 2)))",
            name="result",
        ),
        CheckConstraint(
            "state != 'recovery_required' OR "
            "(outcome = 'inconclusive' AND exit_code = 2)",
            name="recovery",
        ),
        CheckConstraint(
            "state != 'running' OR (claim_id IS NOT NULL AND lease_until IS NOT NULL "
            "AND started_at IS NOT NULL)",
            name="claim",
        ),
        CheckConstraint(
            "(report_content IS NULL AND report_id IS NULL "
            "AND report_sha256 IS NULL) OR "
            "(report_content IS NOT NULL AND report_id IS NOT NULL "
            "AND report_sha256 IS NOT NULL "
            "AND octet_length(report_content) <= 8388608 "
            "AND report_sha256 ~ '^[0-9a-f]{64}$' "
            "AND state IN ('finished','recovery_required'))",
            name="report",
        ),
        Index(
            "uq_audit_runs_outstanding_organization",
            "organization_id",
            unique=True,
            postgresql_where=text("state IN ('queued','running','recovery_required')"),
        ),
    )
    organization_id: Mapped[UUID] = mapped_column(index=True)
    requester_user_id: Mapped[UUID]
    state: Mapped[str] = mapped_column(
        String(24), default="queued", server_default="queued"
    )
    outcome: Mapped[str] = mapped_column(
        String(16), default="unknown", server_default="unknown"
    )
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    claim_id: Mapped[UUID | None]
    lease_until: Mapped[datetime | None]
    error_code: Mapped[str | None] = mapped_column(String(80))
    exit_code: Mapped[int | None]
    report_content: Mapped[str | None] = mapped_column(Text)
    report_id: Mapped[UUID | None]
    report_sha256: Mapped[str | None] = mapped_column(String(64))
