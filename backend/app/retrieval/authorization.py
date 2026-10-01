from dataclasses import replace
from uuid import UUID

from qdrant_client import models
from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from app.documents.extraction import DocumentError
from app.documents.models import Document, DocumentGrant, DocumentVersion
from app.identity.models import User
from app.retrieval.store import field_match
from app.tenancy.models import GroupMember, Membership, Organization
from app.tenancy.scope import AccessScope


def current_scope(scope: AccessScope, session: Session) -> AccessScope:
    session.execute(
        select(Organization.id)
        .where(Organization.id == scope.organization_id)
        .with_for_update(read=True)
    )
    active = session.scalar(
        select(Membership.id)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.id == scope.membership_id,
            Membership.organization_id == scope.organization_id,
            Membership.user_id == scope.user_id,
            Membership.is_active.is_(True),
            User.is_active.is_(True),
        )
    )
    if active is None:
        raise DocumentError("membership_inactive", 401)
    groups = tuple(
        session.scalars(
            select(GroupMember.group_id).where(
                GroupMember.organization_id == scope.organization_id,
                GroupMember.user_id == scope.user_id,
            )
        )
    )
    return replace(scope, group_ids=groups)


def eligible_versions(scope: AccessScope, session: Session) -> tuple[UUID, ...]:
    granted = exists(
        select(DocumentGrant.id).where(
            DocumentGrant.document_id == Document.id,
            DocumentGrant.organization_id == scope.organization_id,
            or_(
                DocumentGrant.user_id == scope.user_id,
                DocumentGrant.group_id.in_(scope.group_ids),
            ),
        )
    )
    return tuple(
        session.scalars(
            select(DocumentVersion.id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                Document.organization_id == scope.organization_id,
                DocumentVersion.organization_id == scope.organization_id,
                Document.state == "ready",
                DocumentVersion.state == "ready",
                Document.current_version_id == DocumentVersion.id,
                or_(Document.visibility == "organization", granted),
            )
            .with_for_update(
                read=True, of=(Document.__table__, DocumentVersion.__table__)
            )
        )
    )


def access_filter(scope: AccessScope, versions: tuple[UUID, ...]) -> models.Filter:
    if not versions:
        raise ValueError("authorized search requires an explicit version allowlist")
    grants: list[models.Condition] = [
        field_match("visibility", "organization"),
        models.FieldCondition(
            key="allowed_user_ids", match=models.MatchAny(any=[str(scope.user_id)])
        ),
    ]
    if scope.group_ids:
        grants.append(
            models.FieldCondition(
                key="allowed_group_ids",
                match=models.MatchAny(any=[str(value) for value in scope.group_ids]),
            )
        )
    return models.Filter(
        must=[
            field_match("organization_id", str(scope.organization_id)),
            field_match("active", True),
            models.FieldCondition(
                key="document_version_id",
                match=models.MatchAny(any=[str(value) for value in versions]),
            ),
            models.Filter(should=grants),
        ]
    )
