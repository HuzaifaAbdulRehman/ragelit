from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import decode_access_token
from app.identity.models import RefreshSession, User
from app.tenancy.enums import Role
from app.tenancy.models import GroupMember, Membership
from app.tenancy.rls import set_request_context


@dataclass(frozen=True, slots=True)
class RequestPrincipal:
    user_id: UUID
    organization_id: UUID
    session_id: UUID
    membership_id: UUID
    role: Role


@dataclass(frozen=True, slots=True)
class AccessScope:
    user_id: UUID
    organization_id: UUID
    membership_id: UUID
    role: Role
    group_ids: tuple[UUID, ...]


class PrincipalError(Exception):
    def __init__(self, code: str = "authentication_failed") -> None:
        super().__init__(code)
        self.code = code


def load_current_principal(
    access_token: str,
    *,
    session: Session,
    settings: Settings,
    now: datetime | None = None,
) -> RequestPrincipal:
    try:
        claims = decode_access_token(
            access_token,
            secret_key=settings.secret_key.get_secret_value(),
        )
        user_id = UUID(claims["sub"])
        organization_id = UUID(claims["org"])
        session_id = UUID(claims["sid"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as error:
        raise PrincipalError from error

    set_request_context(
        session,
        user_id=user_id,
        organization_id=organization_id,
    )
    current_time = now or datetime.now(UTC)
    row = session.execute(
        select(RefreshSession, User, Membership)
        .join(User, User.id == RefreshSession.user_id)
        .join(
            Membership,
            (Membership.user_id == RefreshSession.user_id)
            & (Membership.organization_id == RefreshSession.organization_id),
        )
        .where(
            RefreshSession.id == session_id,
            RefreshSession.user_id == user_id,
            RefreshSession.organization_id == organization_id,
            RefreshSession.revoked_at.is_(None),
            RefreshSession.used_at.is_(None),
            RefreshSession.expires_at > current_time,
        )
    ).one_or_none()
    if row is None:
        raise PrincipalError

    refresh_session, user, membership = row
    if not user.is_active:
        raise PrincipalError
    if not membership.is_active:
        raise PrincipalError("membership_inactive")
    return RequestPrincipal(
        user_id=user.id,
        organization_id=membership.organization_id,
        session_id=refresh_session.id,
        membership_id=membership.id,
        role=membership.role,
    )


def load_access_scope(
    principal: RequestPrincipal,
    *,
    session: Session,
) -> AccessScope:
    group_ids = tuple(
        session.scalars(
            select(GroupMember.group_id)
            .where(
                GroupMember.user_id == principal.user_id,
                GroupMember.organization_id == principal.organization_id,
            )
            .order_by(GroupMember.group_id)
        )
    )
    return AccessScope(
        user_id=principal.user_id,
        organization_id=principal.organization_id,
        membership_id=principal.membership_id,
        role=principal.role,
        group_ids=group_ids,
    )
