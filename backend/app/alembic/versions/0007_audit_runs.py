"""Queue tenant-scoped synthetic audits and preserve validated reports."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_audit_runs"
down_revision: str | None = "0006_intended_document_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("requester_user_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(24), server_default="queued", nullable=False),
        sa.Column("outcome", sa.String(16), server_default="unknown", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("claim_id", sa.Uuid()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("exit_code", sa.Integer()),
        sa.Column("report_content", sa.Text()),
        sa.Column("report_id", sa.Uuid()),
        sa.Column("report_sha256", sa.String(64)),
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
            ["requester_user_id", "organization_id"],
            ["memberships.user_id", "memberships.organization_id"],
            name="fk_audit_runs_membership",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "state IN ('queued','running','finished','recovery_required')", name="state"
        ),
        sa.CheckConstraint(
            "outcome IN ('unknown','pass','fail','inconclusive')", name="outcome"
        ),
        sa.CheckConstraint(
            "(state IN ('queued','running') AND outcome = 'unknown' "
            "AND exit_code IS NULL AND finished_at IS NULL) OR "
            "(state IN ('finished','recovery_required') AND finished_at IS NOT NULL "
            "AND exit_code IS NOT NULL AND ((outcome = 'pass' AND exit_code = 0) OR "
            "(outcome = 'fail' AND exit_code = 1) OR "
            "(outcome = 'inconclusive' AND exit_code = 2)))",
            name="result",
        ),
        sa.CheckConstraint(
            "state != 'recovery_required' OR "
            "(outcome = 'inconclusive' AND exit_code = 2)",
            name="recovery",
        ),
        sa.CheckConstraint(
            "state != 'running' OR (claim_id IS NOT NULL AND lease_until IS NOT NULL "
            "AND started_at IS NOT NULL)",
            name="claim",
        ),
        sa.CheckConstraint(
            "(report_content IS NULL AND report_id IS NULL "
            "AND report_sha256 IS NULL) OR "
            "(report_content IS NOT NULL AND report_id IS NOT NULL "
            "AND report_sha256 IS NOT NULL "
            "AND octet_length(report_content) <= 8388608 "
            "AND report_sha256 ~ '^[0-9a-f]{64}$' "
            "AND state IN ('finished','recovery_required'))",
            name="report",
        ),
    )
    op.create_index("ix_audit_runs_organization_id", "audit_runs", ["organization_id"])
    op.create_index(
        "uq_audit_runs_outstanding_organization",
        "audit_runs",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('queued','running','recovery_required')"),
    )
    op.execute("ALTER TABLE audit_runs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_runs FORCE ROW LEVEL SECURITY")
    match = (
        "organization_id = "
        "NULLIF(current_setting('app.organization_id', true), '')::uuid"
    )
    op.execute(
        f"CREATE POLICY audit_runs_tenant ON audit_runs "
        f"USING ({match}) WITH CHECK ({match})"
    )
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'ragelit_app') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON audit_runs TO ragelit_app;
        END IF; END $$""")


def downgrade() -> None:
    op.drop_table("audit_runs")
