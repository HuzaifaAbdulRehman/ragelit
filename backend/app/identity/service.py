import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import hash_password, issue_access_token, verify_password
from app.identity.models import RefreshSession, User
from app.identity.schemas import LoginCommand, LoginResult, RefreshResult
from app.tenancy.models import Membership, Organization
from app.tenancy.rls import set_refresh_token_context, set_request_context
from app.tenancy.scope import RequestPrincipal

_DUMMY_PASSWORD_HASH = hash_password("ragelit-invalid-login-placeholder")


class AuthError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _token_hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def _new_refresh_token() -> tuple[str, str]:
    raw_token = secrets.token_urlsafe(32)
    return raw_token, _token_hash(raw_token)


def login(
    command: LoginCommand,
    *,
    session: Session,
    settings: Settings,
    now: datetime | None = None,
) -> LoginResult:
    current_time = now or datetime.now(UTC)
    user = session.scalar(select(User).where(User.email == command.email))
    organization = session.scalar(
        select(Organization).where(Organization.slug == command.organization_slug)
    )
    encoded = user.password_hash if user else _DUMMY_PASSWORD_HASH
    password_valid = verify_password(command.password, encoded)
    if user is None or organization is None or not password_valid or not user.is_active:
        raise AuthError("invalid_credentials")

    set_request_context(
        session,
        user_id=user.id,
        organization_id=organization.id,
    )
    membership = session.scalar(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.organization_id == organization.id,
            Membership.is_active.is_(True),
        )
    )
    if membership is None:
        raise AuthError("invalid_credentials")

    raw_token, token_hash = _new_refresh_token()
    expires_at = current_time + timedelta(days=settings.refresh_session_days)
    refresh_session = RefreshSession(
        user_id=user.id,
        organization_id=membership.organization_id,
        family_id=uuid4(),
        token_hash=token_hash,
        expires_at=expires_at,
    )
    session.add(refresh_session)
    session.flush()
    access_token = issue_access_token(
        user.id,
        membership.organization_id,
        refresh_session.id,
        secret_key=settings.secret_key.get_secret_value(),
        lifetime=timedelta(minutes=settings.access_token_minutes),
        issued_at=current_time,
    )
    session.commit()
    return LoginResult(access_token, raw_token, refresh_session.id, expires_at)


def refresh(
    raw_token: str,
    *,
    session: Session,
    settings: Settings,
    now: datetime | None = None,
) -> RefreshResult:
    current_time = now or datetime.now(UTC)
    token_hash = _token_hash(raw_token)
    set_refresh_token_context(session, token_hash=token_hash)
    stored = session.scalar(
        select(RefreshSession)
        .where(RefreshSession.token_hash == token_hash)
        .with_for_update()
    )
    if stored is None:
        raise AuthError("invalid_refresh")

    set_request_context(
        session,
        user_id=stored.user_id,
        organization_id=stored.organization_id,
    )
    row = session.execute(
        select(User, Membership)
        .join(Membership, Membership.user_id == User.id)
        .where(
            User.id == stored.user_id,
            Membership.organization_id == stored.organization_id,
        )
    ).one_or_none()
    if row is None:
        raise AuthError("invalid_refresh")
    user, membership = row
    if stored.used_at is not None:
        session.execute(
            update(RefreshSession)
            .where(RefreshSession.family_id == stored.family_id)
            .values(revoked_at=current_time)
        )
        session.commit()
        raise AuthError("refresh_reused")
    if (
        stored.revoked_at is not None
        or stored.expires_at <= current_time
        or not user.is_active
        or not membership.is_active
    ):
        raise AuthError("invalid_refresh")

    raw_replacement, replacement_hash = _new_refresh_token()
    replacement = RefreshSession(
        user_id=stored.user_id,
        organization_id=stored.organization_id,
        family_id=stored.family_id,
        token_hash=replacement_hash,
        expires_at=current_time + timedelta(days=settings.refresh_session_days),
    )
    session.add(replacement)
    session.flush()
    stored.used_at = current_time
    stored.replaced_by_id = replacement.id
    access_token = issue_access_token(
        stored.user_id,
        stored.organization_id,
        replacement.id,
        secret_key=settings.secret_key.get_secret_value(),
        lifetime=timedelta(minutes=settings.access_token_minutes),
        issued_at=current_time,
    )
    session.commit()
    return RefreshResult(
        access_token,
        raw_replacement,
        replacement.id,
        replacement.expires_at,
    )


def logout(
    session_id: UUID,
    *,
    session: Session,
    now: datetime | None = None,
) -> None:
    session.execute(
        update(RefreshSession)
        .where(RefreshSession.id == session_id, RefreshSession.revoked_at.is_(None))
        .values(revoked_at=now or datetime.now(UTC))
    )
    session.commit()


def logout_refresh(
    raw_token: str, *, session: Session, now: datetime | None = None
) -> None:
    token_hash = _token_hash(raw_token)
    set_refresh_token_context(session, token_hash=token_hash)
    stored = session.scalar(
        select(RefreshSession)
        .where(RefreshSession.token_hash == token_hash)
        .with_for_update()
    )
    if stored is not None and stored.revoked_at is None:
        set_request_context(
            session,
            user_id=stored.user_id,
            organization_id=stored.organization_id,
        )
        stored.revoked_at = now or datetime.now(UTC)
    session.commit()


def switch_organization(
    principal: RequestPrincipal,
    target_id: UUID,
    *,
    session: Session,
    settings: Settings,
    now: datetime | None = None,
) -> RefreshResult:
    current_time = now or datetime.now(UTC)
    current_session = session.scalar(
        select(RefreshSession)
        .where(
            RefreshSession.id == principal.session_id,
            RefreshSession.user_id == principal.user_id,
            RefreshSession.organization_id == principal.organization_id,
            RefreshSession.revoked_at.is_(None),
            RefreshSession.expires_at > current_time,
        )
        .with_for_update()
    )
    if current_session is None:
        raise AuthError("invalid_refresh")
    target_membership = session.scalar(
        select(Membership).where(
            Membership.user_id == principal.user_id,
            Membership.organization_id == target_id,
            Membership.is_active.is_(True),
        )
    )
    if target_membership is None:
        raise AuthError("resource_not_found")
    session.execute(
        update(RefreshSession)
        .where(RefreshSession.family_id == current_session.family_id)
        .values(revoked_at=current_time)
    )
    set_request_context(
        session,
        user_id=principal.user_id,
        organization_id=target_membership.organization_id,
    )
    target_membership = session.scalar(
        select(Membership)
        .where(
            Membership.id == target_membership.id,
            Membership.user_id == principal.user_id,
            Membership.organization_id == target_id,
            Membership.is_active.is_(True),
        )
        .with_for_update()
    )
    if target_membership is None:
        raise AuthError("resource_not_found")
    raw_token, token_hash = _new_refresh_token()
    replacement = RefreshSession(
        user_id=principal.user_id,
        organization_id=target_membership.organization_id,
        family_id=uuid4(),
        token_hash=token_hash,
        expires_at=current_time + timedelta(days=settings.refresh_session_days),
    )
    session.add(replacement)
    session.flush()
    replacement_id = replacement.id
    refresh_expires_at = replacement.expires_at
    access_token = issue_access_token(
        principal.user_id,
        target_membership.organization_id,
        replacement_id,
        secret_key=settings.secret_key.get_secret_value(),
        lifetime=timedelta(minutes=settings.access_token_minutes),
        issued_at=current_time,
    )
    session.commit()
    return RefreshResult(
        access_token,
        raw_token,
        replacement_id,
        refresh_expires_at,
    )
