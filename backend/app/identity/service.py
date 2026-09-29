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
    row = session.execute(
        select(User, Membership, Organization)
        .join(Membership, Membership.user_id == User.id)
        .join(Organization, Organization.id == Membership.organization_id)
        .where(
            User.email == command.email,
            Organization.slug == command.organization_slug,
        )
    ).one_or_none()

    user = row[0] if row else None
    membership = row[1] if row else None
    encoded = user.password_hash if user else _DUMMY_PASSWORD_HASH
    password_valid = verify_password(command.password, encoded)
    if (
        user is None
        or membership is None
        or not password_valid
        or not user.is_active
        or not membership.is_active
    ):
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
    row = session.execute(
        select(RefreshSession, User, Membership)
        .join(User, User.id == RefreshSession.user_id)
        .join(
            Membership,
            (Membership.user_id == RefreshSession.user_id)
            & (Membership.organization_id == RefreshSession.organization_id),
        )
        .where(RefreshSession.token_hash == _token_hash(raw_token))
        .with_for_update()
    ).one_or_none()
    if row is None:
        raise AuthError("invalid_refresh")

    stored, user, membership = row
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
    stored = session.scalar(
        select(RefreshSession)
        .where(RefreshSession.token_hash == _token_hash(raw_token))
        .with_for_update()
    )
    if stored is not None and stored.revoked_at is None:
        stored.revoked_at = now or datetime.now(UTC)
    session.commit()
