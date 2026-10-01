"""Store private query stage evidence without source bodies."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_query_traces"
down_revision: str | None = "0004_document_supersession"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MATCH = (
    "organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid "
    "AND user_id = NULLIF(current_setting('app.user_id', true), '')::uuid"
)


def upgrade() -> None:
    op.create_table(
        "query_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(20), server_default="started", nullable=False),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "organization_id"],
            ["memberships.user_id", "memberships.organization_id"],
            name="fk_query_runs_membership",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "id", "organization_id", "user_id", name="uq_query_runs_id_actor"
        ),
        sa.CheckConstraint(
            "state IN ('started', 'answered', 'abstained', 'failed')", name="state"
        ),
    )
    op.create_table(
        "query_trace_stages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("query_run_id", sa.Uuid(), nullable=False),
        sa.Column("stage", sa.String(30), nullable=False),
        sa.Column("decision", sa.String(80), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column("chunk_ids", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["query_run_id", "organization_id", "user_id"],
            ["query_runs.id", "query_runs.organization_id", "query_runs.user_id"],
            name="fk_query_trace_stages_run_actor",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "query_run_id", "stage", name="uq_query_trace_stages_run_stage"
        ),
        sa.CheckConstraint("duration_ms >= 0", name="duration"),
    )
    for table in ("query_runs", "query_trace_stages"):
        for column in ("organization_id", "user_id"):
            op.create_index(f"ix_{table}_{column}", table, [column])
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY "{table}_actor" ON "{table}" '
            f"USING ({MATCH}) WITH CHECK ({MATCH})"
        )
    op.create_index(
        "ix_query_trace_stages_query_run_id", "query_trace_stages", ["query_run_id"]
    )
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'ragelit_app') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE
                ON query_runs, query_trace_stages TO ragelit_app;
        END IF; END $$""")


def downgrade() -> None:
    op.drop_table("query_trace_stages")
    op.drop_table("query_runs")
