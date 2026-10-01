"""Add document lifecycle and forced tenant policies."""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0003_documents"
down_revision: str | None = "0002_tenant_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("documents", "document_versions", "document_grants", "ingestion_jobs")
MATCH = (
    "organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid"
)


def _base() -> list[sa.Column[Any]]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
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
    ]


def upgrade() -> None:
    op.create_table(
        "documents",
        *_base(),
        sa.Column(
            "organization_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(100), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column(
            "visibility", sa.String(20), server_default="restricted", nullable=False
        ),
        sa.Column("state", sa.String(20), server_default="queued", nullable=False),
        sa.UniqueConstraint(
            "id", "organization_id", name="uq_documents_id_organization"
        ),
        sa.UniqueConstraint(
            "organization_id", "checksum", name="uq_documents_organization_checksum"
        ),
        sa.CheckConstraint(
            "visibility IN ('organization', 'restricted')", name="visibility"
        ),
        sa.CheckConstraint(
            "state IN ('queued', 'processing', 'ready', 'failed', 'deleted')",
            name="state",
        ),
    )
    op.create_table(
        "document_versions",
        *_base(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("storage_key", sa.String(120), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("byte_count", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(20), server_default="queued", nullable=False),
        sa.Column("chunk_count", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id", "organization_id"],
            ["documents.id", "documents.organization_id"],
            name="fk_document_versions_document_organization",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "id", "organization_id", name="uq_document_versions_id_organization"
        ),
        sa.CheckConstraint("byte_count > 0", name="positive_size"),
        sa.CheckConstraint(
            "state IN ('queued', 'processing', 'ready', 'failed', 'deleted')",
            name="state",
        ),
    )
    op.create_table(
        "document_grants",
        *_base(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("group_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["document_id", "organization_id"],
            ["documents.id", "documents.organization_id"],
            name="fk_document_grants_document_organization",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "organization_id"],
            ["memberships.user_id", "memberships.organization_id"],
            name="fk_document_grants_membership",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["group_id", "organization_id"],
            ["groups.id", "groups.organization_id"],
            name="fk_document_grants_group_organization",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "document_id", "user_id", name="uq_document_grants_document_user"
        ),
        sa.UniqueConstraint(
            "document_id", "group_id", name="uq_document_grants_document_group"
        ),
        sa.CheckConstraint(
            "(user_id IS NULL) <> (group_id IS NULL)", name="one_target"
        ),
    )
    op.create_table(
        "ingestion_jobs",
        *_base(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(20), server_default="queued", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("claim_id", sa.Uuid(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.ForeignKeyConstraint(
            ["version_id", "organization_id"],
            ["document_versions.id", "document_versions.organization_id"],
            name="fk_ingestion_jobs_version_organization",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("version_id", name="uq_ingestion_jobs_version"),
        sa.CheckConstraint(
            "state IN ('queued', 'processing', 'ready', 'failed')", name="state"
        ),
    )
    for table in TABLES:
        op.create_index(f"ix_{table}_organization_id", table, ["organization_id"])
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY "{table}_tenant" ON "{table}" '
            f"USING ({MATCH}) WITH CHECK ({MATCH})"
        )
    for table, column in (
        ("document_versions", "document_id"),
        ("document_grants", "document_id"),
        ("ingestion_jobs", "version_id"),
    ):
        op.create_index(f"ix_{table}_{column}", table, [column])
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'ragelit_app') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON
                documents, document_versions, document_grants, ingestion_jobs
                TO ragelit_app;
        END IF; END $$""")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
