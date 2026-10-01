from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Document(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_documents_id_organization"),
        UniqueConstraint(
            "organization_id", "checksum", name="uq_documents_organization_checksum"
        ),
        CheckConstraint(
            "visibility IN ('organization', 'restricted')", name="visibility"
        ),
        CheckConstraint(
            "state IN ('queued', 'processing', 'ready', 'failed', 'deleted')",
            name="state",
        ),
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(100))
    checksum: Mapped[str] = mapped_column(String(64))
    visibility: Mapped[str] = mapped_column(
        String(20), default="restricted", server_default="restricted"
    )
    state: Mapped[str] = mapped_column(
        String(20), default="queued", server_default="queued"
    )


class DocumentVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "organization_id"],
            ["documents.id", "documents.organization_id"],
            name="fk_document_versions_document_organization",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "id", "organization_id", name="uq_document_versions_id_organization"
        ),
        CheckConstraint("byte_count > 0", name="positive_size"),
        CheckConstraint(
            "state IN ('queued', 'processing', 'ready', 'failed', 'deleted')",
            name="state",
        ),
    )
    organization_id: Mapped[UUID] = mapped_column(index=True)
    document_id: Mapped[UUID] = mapped_column(index=True)
    storage_key: Mapped[str] = mapped_column(String(120))
    checksum: Mapped[str] = mapped_column(String(64))
    byte_count: Mapped[int]
    state: Mapped[str] = mapped_column(
        String(20), default="queued", server_default="queued"
    )
    chunk_count: Mapped[int] = mapped_column(default=0, server_default="0")


class DocumentGrant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "document_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "organization_id"],
            ["documents.id", "documents.organization_id"],
            name="fk_document_grants_document_organization",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["user_id", "organization_id"],
            ["memberships.user_id", "memberships.organization_id"],
            name="fk_document_grants_membership",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["group_id", "organization_id"],
            ["groups.id", "groups.organization_id"],
            name="fk_document_grants_group_organization",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "document_id", "user_id", name="uq_document_grants_document_user"
        ),
        UniqueConstraint(
            "document_id", "group_id", name="uq_document_grants_document_group"
        ),
        CheckConstraint("(user_id IS NULL) <> (group_id IS NULL)", name="one_target"),
    )
    organization_id: Mapped[UUID] = mapped_column(index=True)
    document_id: Mapped[UUID] = mapped_column(index=True)
    user_id: Mapped[UUID | None]
    group_id: Mapped[UUID | None]


class IngestionJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["version_id", "organization_id"],
            ["document_versions.id", "document_versions.organization_id"],
            name="fk_ingestion_jobs_version_organization",
            ondelete="CASCADE",
        ),
        UniqueConstraint("version_id", name="uq_ingestion_jobs_version"),
        CheckConstraint(
            "state IN ('queued', 'processing', 'ready', 'failed')", name="state"
        ),
    )
    organization_id: Mapped[UUID] = mapped_column(index=True)
    version_id: Mapped[UUID] = mapped_column(index=True)
    state: Mapped[str] = mapped_column(
        String(20), default="queued", server_default="queued"
    )
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    claim_id: Mapped[UUID | None]
    lease_until: Mapped[datetime | None]
    error_code: Mapped[str | None] = mapped_column(String(80))
