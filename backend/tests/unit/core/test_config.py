import pytest
from pydantic import ValidationError

from app.core.config import Settings


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "secret_key": "test-secret",
        "database_admin_url": "postgresql+psycopg://admin:test@db/ragelit",
        "database_url": "postgresql+psycopg://app:test@db/ragelit",
        "qdrant_url": "http://qdrant:6333",
        "cookie_secure": False,
    }
    values.update(overrides)
    return Settings.model_validate(values)


def test_production_rejects_default_secret() -> None:
    with pytest.raises(ValidationError):
        _settings(
            environment="production",
            secret_key="change-me",
            cookie_secure=True,
        )


def test_production_requires_secure_cookie() -> None:
    with pytest.raises(ValidationError):
        _settings(
            environment="production",
            secret_key="a-production-secret-that-is-long-enough",
            cookie_secure=False,
        )


@pytest.mark.parametrize(
    "field",
    ["access_token_minutes", "refresh_session_days", "max_upload_bytes"],
)
def test_positive_limits_reject_zero(field: str) -> None:
    with pytest.raises(ValidationError):
        _settings(**{field: 0})


def test_local_defaults_match_the_security_contract() -> None:
    settings = _settings()

    assert settings.access_token_minutes == 15
    assert settings.refresh_session_days == 14
    assert settings.max_upload_bytes == 25 * 1024 * 1024
