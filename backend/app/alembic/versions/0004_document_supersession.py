"""Retain replaced document versions without making them retrievable."""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_document_supersession"
down_revision: str | None = "0003_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_document_versions_state"), "document_versions", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_document_versions_state"),
        "document_versions",
        "state IN ('queued', 'processing', 'ready', 'failed', 'deleted', 'superseded')",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE document_versions SET state = 'deleted' WHERE state = 'superseded'"
    )
    op.drop_constraint(
        op.f("ck_document_versions_state"), "document_versions", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_document_versions_state"),
        "document_versions",
        "state IN ('queued', 'processing', 'ready', 'failed', 'deleted')",
    )
