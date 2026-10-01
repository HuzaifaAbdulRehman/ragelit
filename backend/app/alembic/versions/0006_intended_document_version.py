"""Fence document publication to its intended version."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_intended_document_version"
down_revision: str | None = "0005_query_traces"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents", sa.Column("current_version_id", sa.Uuid(), nullable=True)
    )
    op.create_unique_constraint(
        "uq_document_versions_id_parent",
        "document_versions",
        ["id", "document_id", "organization_id"],
    )
    for table in ("documents", "document_versions"):
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
    op.execute("""UPDATE documents d SET current_version_id = (
        SELECT v.id FROM document_versions v
        WHERE v.document_id = d.id AND v.organization_id = d.organization_id
          AND v.checksum = d.checksum
        ORDER BY v.created_at DESC, v.id DESC LIMIT 1
    )""")
    for table in ("documents", "document_versions"):
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
    op.create_foreign_key(
        "fk_documents_current_version",
        "documents",
        "document_versions",
        ["current_version_id", "id", "organization_id"],
        ["id", "document_id", "organization_id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_documents_current_version", "documents", type_="foreignkey")
    op.drop_constraint(
        "uq_document_versions_id_parent", "document_versions", type_="unique"
    )
    op.drop_column("documents", "current_version_id")
