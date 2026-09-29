from uuid import UUID

from sqlalchemy import (
    Boolean,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.tenancy.enums import Role


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organizations"
    __table_args__ = (UniqueConstraint("slug", name="uq_organizations_slug"),)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)


class Membership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "organization_id",
            name="uq_memberships_user_organization",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[Role] = mapped_column(
        Enum(
            Role,
            name="role",
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Group(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "groups"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "name",
            name="uq_groups_organization_name",
        ),
        UniqueConstraint("id", "organization_id", name="uq_groups_id_organization"),
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)


class GroupMember(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "group_members"
    __table_args__ = (
        ForeignKeyConstraint(
            ["group_id", "organization_id"],
            ["groups.id", "groups.organization_id"],
            name="fk_group_members_group_organization",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["user_id", "organization_id"],
            ["memberships.user_id", "memberships.organization_id"],
            name="fk_group_members_membership",
            ondelete="CASCADE",
        ),
        UniqueConstraint("group_id", "user_id", name="uq_group_members_group_user"),
    )

    organization_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    group_id: Mapped[UUID] = mapped_column(nullable=False)
    user_id: Mapped[UUID] = mapped_column(nullable=False)
