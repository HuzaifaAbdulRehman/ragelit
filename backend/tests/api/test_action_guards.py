from collections.abc import Callable
from typing import Annotated
from uuid import UUID

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_current_principal, require_action
from app.core.config import Settings
from app.main import create_app
from app.tenancy.enums import Role
from app.tenancy.policy import Action, role_allows
from app.tenancy.scope import RequestPrincipal

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
ORGANIZATION_ID = UUID("22222222-2222-2222-2222-222222222222")
SESSION_ID = UUID("33333333-3333-3333-3333-333333333333")
MEMBERSHIP_ID = UUID("44444444-4444-4444-4444-444444444444")


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "environment": "test",
            "secret_key": "guard-test-secret-that-is-at-least-32-bytes",
            "database_admin_url": "unused",
            "database_url": "unused",
            "qdrant_url": "http://qdrant:6333",
        }
    )


def _guarded_route(
    guard: Callable[..., RequestPrincipal],
) -> Callable[..., dict[str, str]]:
    def route(
        principal: Annotated[RequestPrincipal, Depends(guard)],
    ) -> dict[str, str]:
        return {"user_id": str(principal.user_id)}

    return route


def _guard_app(role: Role | None) -> FastAPI:
    app = create_app(_settings(), session_factory=sessionmaker())
    for action in Action:
        app.add_api_route(
            f"/test/actions/{action.value}",
            _guarded_route(require_action(action)),
            methods=["GET"],
            name=f"test_{action.value}",
        )

    if role is not None:
        principal = RequestPrincipal(
            user_id=USER_ID,
            organization_id=ORGANIZATION_ID,
            session_id=SESSION_ID,
            membership_id=MEMBERSHIP_ID,
            role=role,
        )

        def override_principal() -> RequestPrincipal:
            return principal

        app.dependency_overrides[get_current_principal] = override_principal
    return app


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("action", list(Action))
def test_action_guard_uses_the_fixed_role_matrix(role: Role, action: Action) -> None:
    with TestClient(_guard_app(role)) as client:
        response = client.get(f"/test/actions/{action.value}")

    if role_allows(role, action):
        assert response.status_code == 200
        assert response.json() == {"user_id": str(USER_ID)}
    else:
        assert response.status_code == 403
        assert response.headers["content-type"].startswith(
            "application/problem+json"
        )
        assert response.json()["code"] == "action_forbidden"


def test_authentication_failure_wins_before_authorization() -> None:
    with TestClient(_guard_app(None)) as client:
        response = client.get(
            f"/test/actions/{Action.CHAT_USE.value}",
            headers={"Authorization": "Bearer not-a-token"},
        )

    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["code"] == "authentication_failed"
