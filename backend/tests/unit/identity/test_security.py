from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest

from app.core.security import (
    decode_access_token,
    hash_password,
    issue_access_token,
    verify_password,
)

TEST_SECRET = "unit-test-secret-that-is-at-least-32-bytes"


def test_password_hash_uses_argon2_and_verifies() -> None:
    encoded = hash_password("correct horse battery staple")

    assert encoded != "correct horse battery staple"
    assert encoded.startswith("$argon2")
    assert verify_password("correct horse battery staple", encoded)
    assert not verify_password("wrong password", encoded)
    assert not verify_password("wrong password", "not-a-password-hash")


def test_access_token_contains_only_identity_scope_claims() -> None:
    user_id = uuid4()
    organization_id = uuid4()
    session_id = uuid4()
    issued_at = datetime.now(UTC)

    token = issue_access_token(
        user_id,
        organization_id,
        session_id,
        secret_key=TEST_SECRET,
        issued_at=issued_at,
        lifetime=timedelta(minutes=15),
    )
    claims = decode_access_token(token, secret_key=TEST_SECRET)

    assert claims["sub"] == str(user_id)
    assert claims["org"] == str(organization_id)
    assert claims["sid"] == str(session_id)
    assert claims["iat"] == int(issued_at.timestamp())
    assert claims["exp"] == int((issued_at + timedelta(minutes=15)).timestamp())
    assert "role" not in claims
    assert "groups" not in claims


def test_expired_access_token_is_rejected() -> None:
    token = issue_access_token(
        uuid4(),
        uuid4(),
        uuid4(),
        secret_key=TEST_SECRET,
        issued_at=datetime.now(UTC) - timedelta(hours=1),
        lifetime=timedelta(minutes=1),
    )

    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(token, secret_key=TEST_SECRET)
