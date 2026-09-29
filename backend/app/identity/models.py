from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    @validates("email")
    def normalize_email(self, _key: str, value: str) -> str:
        return value.strip().lower()


class RefreshSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "refresh_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "organization_id"],
            ["memberships.user_id", "memberships.organization_id"],
            name="fk_refresh_sessions_membership",
            ondelete="CASCADE",
        ),
        UniqueConstraint("token_hash", name="uq_refresh_sessions_token_hash"),
    )

    user_id: Mapped[UUID] = mapped_column(nullable=False)
    organization_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    family_id: Mapped[UUID] = mapped_column(default=uuid4, nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(nullable=True)
    replaced_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("refresh_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
