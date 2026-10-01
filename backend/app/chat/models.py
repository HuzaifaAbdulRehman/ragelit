from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class QueryRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "query_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "organization_id"],
            ["memberships.user_id", "memberships.organization_id"],
            name="fk_query_runs_membership",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "id", "organization_id", "user_id", name="uq_query_runs_id_actor"
        ),
        CheckConstraint(
            "state IN ('started', 'answered', 'abstained', 'failed')", name="state"
        ),
    )
    organization_id: Mapped[UUID] = mapped_column(index=True)
    user_id: Mapped[UUID] = mapped_column(index=True)
    state: Mapped[str] = mapped_column(
        String(20), default="started", server_default="started"
    )
    error_code: Mapped[str | None] = mapped_column(String(80))


class QueryTraceStage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "query_trace_stages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["query_run_id", "organization_id", "user_id"],
            ["query_runs.id", "query_runs.organization_id", "query_runs.user_id"],
            name="fk_query_trace_stages_run_actor",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "query_run_id", "stage", name="uq_query_trace_stages_run_stage"
        ),
        CheckConstraint("duration_ms >= 0", name="duration"),
    )
    organization_id: Mapped[UUID] = mapped_column(index=True)
    user_id: Mapped[UUID] = mapped_column(index=True)
    query_run_id: Mapped[UUID] = mapped_column(index=True)
    stage: Mapped[str] = mapped_column(String(30))
    decision: Mapped[str] = mapped_column(String(80))
    duration_ms: Mapped[float]
    chunk_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
