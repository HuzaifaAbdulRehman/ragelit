import pytest

from app.core.config import Settings
from tests.e2e_app import validate_fixture_settings


def fixture_settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "secret_key": "e2e-secret-that-is-at-least-32-bytes",
        "database_admin_url": "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/ragelit_e2e",
        "database_url": "postgresql+psycopg://ragelit_app:ragelit_app@127.0.0.1:5432/ragelit_e2e",
        "qdrant_url": "http://127.0.0.1:6333",
        "qdrant_collection": "ragelit_e2e",
    }
    values.update(changes)
    return Settings.model_validate(values)


def test_dedicated_e2e_settings_are_allowed() -> None:
    validate_fixture_settings(fixture_settings())


@pytest.mark.parametrize(
    "changes",
    [
        {"environment": "local"},
        {"database_url": "postgresql+psycopg://localhost/production"},
        {"database_admin_url": "postgresql+psycopg://localhost/production"},
        {"qdrant_collection": "ragelit_chunks"},
    ],
)
def test_e2e_guard_rejects_nonfixture_targets(changes: dict[str, object]) -> None:
    with pytest.raises(RuntimeError, match="dedicated"):
        validate_fixture_settings(fixture_settings(**changes))
