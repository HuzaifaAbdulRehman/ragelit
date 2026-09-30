import hashlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import decode_access_token
from app.identity.api import REFRESH_COOKIE
from app.identity.models import RefreshSession
from app.identity.service import AuthError, switch_organization
from app.tenancy.enums import Role
from app.tenancy.rls import set_request_context
from app.tenancy.scope import RequestPrincipal
from tests.api.tenant_support import TenantApiSeed


def test_user_lists_only_their_active_organization_memberships(
    tenant_seed: TenantApiSeed,
    tenant_client: TestClient,
    login_headers: Callable[[str, str], dict[str, str]],
) -> None:
    headers = login_headers(tenant_seed.owner_email, tenant_seed.organization_a_slug)

    response = tenant_client.get("/api/v1/organizations", headers=headers)

    assert response.status_code == 200
    assert {item["id"] for item in response.json()["items"]} == {
        str(tenant_seed.organization_a_id),
        str(tenant_seed.organization_b_id),
    }


def test_switch_organization_rotates_to_a_new_session_family(
    tenant_seed: TenantApiSeed,
    tenant_client: TestClient,
    login_headers: Callable[[str, str], dict[str, str]],
    tenant_database_engines: tuple[Engine, Engine],
    tenant_settings: Settings,
) -> None:
    headers = login_headers(tenant_seed.owner_email, tenant_seed.organization_a_slug)
    old_access_token = headers["Authorization"].removeprefix("Bearer ")
    old_refresh_token = tenant_client.cookies[REFRESH_COOKIE]
    old_claims = decode_access_token(
        old_access_token,
        secret_key=tenant_settings.secret_key.get_secret_value(),
    )
    available = tenant_client.get("/api/v1/organizations", headers=headers)
    assert str(tenant_seed.organization_b_id) in {
        item["id"] for item in available.json()["items"]
    }

    response = tenant_client.post(
        "/api/v1/auth/switch-organization",
        headers=headers,
        json={"target_organization_id": str(tenant_seed.organization_b_id)},
    )

    assert response.status_code == 200
    new_access_token = response.json()["access_token"]
    new_claims = decode_access_token(
        new_access_token,
        secret_key=tenant_settings.secret_key.get_secret_value(),
    )
    assert new_claims["org"] == str(tenant_seed.organization_b_id)
    assert tenant_client.cookies[REFRESH_COOKIE] != old_refresh_token
    admin_engine = tenant_database_engines[0]
    with Session(admin_engine) as session:
        old_session = session.get(RefreshSession, UUID(old_claims["sid"]))
        new_session = session.get(RefreshSession, UUID(new_claims["sid"]))
    assert old_session is not None
    assert new_session is not None
    assert old_session.revoked_at is not None
    assert old_session.family_id != new_session.family_id
    old_access = tenant_client.get("/api/v1/organizations", headers=headers)
    assert old_access.status_code == 401
    assert old_access.json()["code"] == "authentication_failed"


@pytest.mark.parametrize("target", ["inactive", "cross_user"])
def test_switch_hides_unavailable_organizations(
    tenant_seed: TenantApiSeed,
    tenant_client: TestClient,
    login_headers: Callable[[str, str], dict[str, str]],
    target: str,
) -> None:
    headers = login_headers(tenant_seed.owner_email, tenant_seed.organization_a_slug)
    if target == "inactive":
        target_id = tenant_seed.organization_d_id
        secret_name = tenant_seed.organization_d_name
    else:
        target_id = tenant_seed.organization_c_id
        secret_name = tenant_seed.organization_c_name

    response = tenant_client.post(
        "/api/v1/auth/switch-organization",
        headers=headers,
        json={"target_organization_id": str(target_id)},
    )

    body = response.text
    assert response.status_code == 404
    assert response.json()["code"] == "resource_not_found"
    assert str(target_id) not in body
    assert secret_name not in body


def test_old_refresh_token_cannot_be_replayed_after_switch(
    tenant_seed: TenantApiSeed,
    tenant_client: TestClient,
    login_headers: Callable[[str, str], dict[str, str]],
    tenant_database_engines: tuple[Engine, Engine],
) -> None:
    headers = login_headers(tenant_seed.owner_email, tenant_seed.organization_a_slug)
    old_refresh_token = tenant_client.cookies[REFRESH_COOKIE]
    response = tenant_client.post(
        "/api/v1/auth/switch-organization",
        headers=headers,
        json={"target_organization_id": str(tenant_seed.organization_b_id)},
    )
    assert response.status_code == 200
    tenant_client.cookies.set(
        REFRESH_COOKIE,
        old_refresh_token,
        path="/api/v1/auth",
    )

    replay = tenant_client.post("/api/v1/auth/refresh")

    assert replay.status_code == 401
    assert replay.json()["code"] == "invalid_refresh"
    admin_engine = tenant_database_engines[0]
    with Session(admin_engine) as session:
        stored = session.scalar(
            select(RefreshSession).where(
                RefreshSession.token_hash
                == hashlib.sha256(old_refresh_token.encode()).hexdigest()
            )
        )
        assert stored is not None
        assert stored.revoked_at is not None


def test_concurrent_switch_uses_the_current_session_once(
    tenant_seed: TenantApiSeed,
    tenant_client: TestClient,
    login_headers: Callable[[str, str], dict[str, str]],
    tenant_database_engines: tuple[Engine, Engine],
    tenant_settings: Settings,
) -> None:
    headers = login_headers(tenant_seed.owner_email, tenant_seed.organization_a_slug)
    claims = decode_access_token(
        headers["Authorization"].removeprefix("Bearer "),
        secret_key=tenant_settings.secret_key.get_secret_value(),
    )
    principal = RequestPrincipal(
        user_id=UUID(claims["sub"]),
        organization_id=tenant_seed.organization_a_id,
        session_id=UUID(claims["sid"]),
        membership_id=tenant_seed.owner_membership_a_id,
        role=Role.OWNER,
    )
    application_engine = tenant_database_engines[1]

    def attempt_switch() -> str:
        with Session(application_engine) as session:
            set_request_context(
                session,
                user_id=principal.user_id,
                organization_id=principal.organization_id,
            )
            try:
                switch_organization(
                    principal,
                    tenant_seed.organization_b_id,
                    session=session,
                    settings=tenant_settings,
                )
            except AuthError as error:
                return error.code
            return "switched"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _index: attempt_switch(), range(2)))

    assert sorted(outcomes) == ["invalid_refresh", "switched"]
