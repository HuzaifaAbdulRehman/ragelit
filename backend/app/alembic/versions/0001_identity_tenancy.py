"""Create identity and tenancy tables."""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_identity_tenancy"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

role_enum = postgresql.ENUM(
    "owner",
    "admin",
    "auditor",
    "member",
    name="role",
    create_type=False,
)


def _timestamps() -> tuple[sa.Column[datetime], sa.Column[datetime]]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )


def upgrade() -> None:
    role_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_organizations"),
        sa.UniqueConstraint("slug", name="uq_organizations_slug"),
    )
    op.create_table(
        "memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("role", role_enum, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_memberships_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_memberships_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_memberships"),
        sa.UniqueConstraint(
            "user_id",
            "organization_id",
            name="uq_memberships_user_organization",
        ),
    )
    op.create_index(
        "ix_memberships_organization_id",
        "memberships",
        ["organization_id"],
    )
    op.create_table(
        "groups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_groups_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_groups"),
        sa.UniqueConstraint(
            "id",
            "organization_id",
            name="uq_groups_id_organization",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "name",
            name="uq_groups_organization_name",
        ),
    )
    op.create_index("ix_groups_organization_id", "groups", ["organization_id"])
    op.create_table(
        "group_members",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["group_id", "organization_id"],
            ["groups.id", "groups.organization_id"],
            name="fk_group_members_group_organization",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "organization_id"],
            ["memberships.user_id", "memberships.organization_id"],
            name="fk_group_members_membership",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_group_members"),
        sa.UniqueConstraint(
            "group_id",
            "user_id",
            name="uq_group_members_group_user",
        ),
    )
    op.create_index(
        "ix_group_members_organization_id",
        "group_members",
        ["organization_id"],
    )
    op.create_table(
        "refresh_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by_id", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["replaced_by_id"],
            ["refresh_sessions.id"],
            name="fk_refresh_sessions_replaced_by_id_refresh_sessions",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "organization_id"],
            ["memberships.user_id", "memberships.organization_id"],
            name="fk_refresh_sessions_membership",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_refresh_sessions"),
        sa.UniqueConstraint("token_hash", name="uq_refresh_sessions_token_hash"),
    )
    op.create_index(
        "ix_refresh_sessions_family_id",
        "refresh_sessions",
        ["family_id"],
    )
    op.create_index(
        "ix_refresh_sessions_organization_id",
        "refresh_sessions",
        ["organization_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_refresh_sessions_organization_id", table_name="refresh_sessions")
    op.drop_index("ix_refresh_sessions_family_id", table_name="refresh_sessions")
    op.drop_table("refresh_sessions")
    op.drop_index("ix_group_members_organization_id", table_name="group_members")
    op.drop_table("group_members")
    op.drop_index("ix_groups_organization_id", table_name="groups")
    op.drop_table("groups")
    op.drop_index("ix_memberships_organization_id", table_name="memberships")
    op.drop_table("memberships")
    op.drop_table("organizations")
    op.drop_table("users")
    role_enum.drop(op.get_bind(), checkfirst=True)
