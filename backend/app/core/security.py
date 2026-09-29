from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict, cast
from uuid import UUID

import jwt
from pwdlib import PasswordHash
from pwdlib.exceptions import PwdlibError

from app.core.config import Settings

JWT_ALGORITHM = "HS256"

password_hash = PasswordHash.recommended()


class AccessTokenClaims(TypedDict):
    sub: str
    org: str
    sid: str
    iat: int
    exp: int


def _load_settings() -> Settings:
    settings_factory = cast(Callable[[], Settings], Settings)
    return settings_factory()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    try:
        return password_hash.verify(password, encoded)
    except PwdlibError:
        return False


def issue_access_token(
    user_id: UUID,
    organization_id: UUID,
    session_id: UUID,
    *,
    secret_key: str | None = None,
    issued_at: datetime | None = None,
    lifetime: timedelta | None = None,
) -> str:
    if secret_key is None or lifetime is None:
        settings = _load_settings()
        if secret_key is None:
            secret_key = settings.secret_key.get_secret_value()
        if lifetime is None:
            lifetime = timedelta(minutes=settings.access_token_minutes)

    assert secret_key is not None
    assert lifetime is not None
    now = issued_at or datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "org": str(organization_id),
        "sid": str(session_id),
        "iat": int(now.timestamp()),
        "exp": int((now + lifetime).timestamp()),
    }
    return jwt.encode(claims, secret_key, algorithm=JWT_ALGORITHM)


def decode_access_token(
    token: str,
    *,
    secret_key: str | None = None,
) -> AccessTokenClaims:
    signing_key = secret_key or _load_settings().secret_key.get_secret_value()
    claims: dict[str, Any] = jwt.decode(
        token,
        signing_key,
        algorithms=[JWT_ALGORITHM],
        options={"require": ["sub", "org", "sid", "iat", "exp"]},
    )
    return cast(AccessTokenClaims, claims)
