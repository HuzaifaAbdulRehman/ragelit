from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "environment": "test",
            "secret_key": "test-secret",
            "database_admin_url": "postgresql+psycopg://admin:test@db/ragelit",
            "database_url": "postgresql+psycopg://app:test@db/ragelit",
            "qdrant_url": "http://qdrant:6333",
        }
    )


def test_liveness_has_no_dependency_checks() -> None:
    calls = 0

    def dependency_check() -> bool:
        nonlocal calls
        calls += 1
        return True

    app = create_app(_settings(), {"postgres": dependency_check})

    with TestClient(app) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert calls == 0


def test_readiness_reports_healthy_dependencies() -> None:
    checks: dict[str, Callable[[], bool]] = {
        "postgres": lambda: True,
        "qdrant": lambda: True,
    }
    app = create_app(_settings(), checks)

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "dependencies": {"postgres": "ok", "qdrant": "ok"},
    }


def test_readiness_without_checks_fails_closed() -> None:
    app = create_app(_settings())

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["code"] == "readiness_not_configured"


@pytest.mark.parametrize("failure", [lambda: False, lambda: 1 / 0])
def test_readiness_reports_failed_dependency_without_error_details(
    failure: Callable[[], bool],
) -> None:
    app = create_app(
        _settings(),
        {"postgres": lambda: True, "qdrant": failure},
    )

    with TestClient(app) as client:
        response = client.get("/health/ready")

    body = response.json()
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert body["code"] == "dependency_unavailable"
    assert body["status"] == 503
    assert body["dependencies"] == {"postgres": "ok", "qdrant": "unavailable"}
    assert "division" not in response.text.lower()
