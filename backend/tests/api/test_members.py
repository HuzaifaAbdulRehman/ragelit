from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import decode_access_token
from app.tenancy.enums import Role
from app.tenancy.models import Membership
from app.tenancy.rls import set_request_context
from app.tenancy.scope import RequestPrincipal
from app.tenancy.service import TenancyError, change_member_role
from tests.api.tenant_support import TenantApiSeed


@pytest.mark.parametrize("actor", ["owner", "admin"])
def test_owner_and_admin_can_manage_members(
    tenant_seed: TenantApiSeed,
    tenant_client: TestClient,
    login_headers: Callable[[str, str], dict[str, str]],
    actor: str,
) -> None:
    email = tenant_seed.owner_email if actor == "owner" else tenant_seed.admin_email
    headers = login_headers(email, tenant_seed.organization_a_slug)
    base = f"/api/v1/organizations/{tenant_seed.organization_a_id}/members"

    listed = tenant_client.get(f"{base}?limit=2&offset=0", headers=headers)
    changed = tenant_client.patch(
        f"{base}/{tenant_seed.member_membership_id}/role",
        headers=headers,
        json={"role": Role.AUDITOR},
    )

    assert listed.status_code == 200
    assert listed.json()["limit"] == 2
    assert len(listed.json()["items"]) == 2
    assert changed.status_code == 200
    assert changed.json()["role"] == Role.AUDITOR
    assert "password" not in changed.text.lower()


@pytest.mark.parametrize("actor", ["auditor", "member"])
def test_auditor_and_member_cannot_manage_members(
    tenant_seed: TenantApiSeed,
    tenant_client: TestClient,
    login_headers: Callable[[str, str], dict[str, str]],
    actor: str,
) -> None:
    email = (
        tenant_seed.auditor_email if actor == "auditor" else tenant_seed.member_email
    )
    headers = login_headers(email, tenant_seed.organization_a_slug)

    response = tenant_client.patch(
        f"/api/v1/organizations/{tenant_seed.organization_a_id}"
        f"/members/{tenant_seed.member_membership_id}/active",
        headers=headers,
        json={"is_active": False},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "action_forbidden"


@pytest.mark.parametrize("actor", ["auditor", "member"])
def test_role_denial_does_not_reveal_another_organization(
    tenant_seed: TenantApiSeed,
    tenant_client: TestClient,
    login_headers: Callable[[str, str], dict[str, str]],
    actor: str,
) -> None:
    email = (
        tenant_seed.auditor_email if actor == "auditor" else tenant_seed.member_email
    )
    headers = login_headers(email, tenant_seed.organization_a_slug)

    response = tenant_client.get(
        f"/api/v1/organizations/{tenant_seed.organization_b_id}/members",
        headers=headers,
    )

    assert response.status_code == 404
    assert response.json()["code"] == "resource_not_found"
    assert str(tenant_seed.organization_b_id) not in response.text


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", "other_org", None),
        ("PATCH", "other_org_role", {"role": Role.ADMIN}),
        ("PATCH", "foreign_member", {"role": Role.ADMIN}),
    ],
)
def test_member_routes_hide_other_tenants(
    tenant_seed: TenantApiSeed,
    tenant_client: TestClient,
    login_headers: Callable[[str, str], dict[str, str]],
    method: str,
    path: str,
    payload: dict[str, object] | None,
) -> None:
    headers = login_headers(tenant_seed.owner_email, tenant_seed.organization_a_slug)
    if path == "other_org":
        url = f"/api/v1/organizations/{tenant_seed.organization_b_id}/members"
    elif path == "other_org_role":
        url = (
            f"/api/v1/organizations/{tenant_seed.organization_b_id}"
            f"/members/{tenant_seed.outsider_membership_b_id}/role"
        )
    else:
        url = (
            f"/api/v1/organizations/{tenant_seed.organization_a_id}"
            f"/members/{tenant_seed.outsider_membership_b_id}/role"
        )

    response = tenant_client.request(method, url, headers=headers, json=payload)

    body = response.text
    assert response.status_code == 404
    assert response.json()["code"] == "resource_not_found"
    assert tenant_seed.organization_b_name not in body
    assert tenant_seed.outsider_email not in body
    assert str(tenant_seed.organization_b_id) not in body
    assert str(tenant_seed.outsider_membership_b_id) not in body


@pytest.mark.parametrize(
    ("endpoint", "payload"),
    [
        ("role", {"role": Role.ADMIN}),
        ("active", {"is_active": False}),
    ],
)
def test_last_active_owner_cannot_be_removed(
    tenant_seed: TenantApiSeed,
    tenant_client: TestClient,
    login_headers: Callable[[str, str], dict[str, str]],
    endpoint: str,
    payload: dict[str, object],
) -> None:
    headers = login_headers(tenant_seed.owner_email, tenant_seed.organization_a_slug)

    response = tenant_client.patch(
        f"/api/v1/organizations/{tenant_seed.organization_a_id}"
        f"/members/{tenant_seed.owner_membership_a_id}/{endpoint}",
        headers=headers,
        json=payload,
    )

    assert response.status_code == 409
    assert response.json()["code"] == "last_owner_required"


def test_admin_cannot_grant_owner_role(
    tenant_seed: TenantApiSeed,
    tenant_client: TestClient,
    login_headers: Callable[[str, str], dict[str, str]],
) -> None:
    headers = login_headers(tenant_seed.admin_email, tenant_seed.organization_a_slug)

    response = tenant_client.patch(
        f"/api/v1/organizations/{tenant_seed.organization_a_id}"
        f"/members/{tenant_seed.member_membership_id}/role",
        headers=headers,
        json={"role": Role.OWNER},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "action_forbidden"


@pytest.mark.parametrize(
    ("endpoint", "payload"),
    [
        ("role", {"role": Role.ADMIN}),
        ("active", {"is_active": False}),
    ],
)
def test_admin_cannot_change_an_owner(
    tenant_seed: TenantApiSeed,
    tenant_client: TestClient,
    login_headers: Callable[[str, str], dict[str, str]],
    endpoint: str,
    payload: dict[str, object],
) -> None:
    headers = login_headers(tenant_seed.admin_email, tenant_seed.organization_a_slug)

    response = tenant_client.patch(
        f"/api/v1/organizations/{tenant_seed.organization_a_id}"
        f"/members/{tenant_seed.owner_membership_a_id}/{endpoint}",
        headers=headers,
        json=payload,
    )

    assert response.status_code == 403
    assert response.json()["code"] == "action_forbidden"


def test_concurrent_owner_changes_keep_one_active_owner(
    tenant_seed: TenantApiSeed,
    tenant_client: TestClient,
    login_headers: Callable[[str, str], dict[str, str]],
    tenant_database_engines: tuple[Engine, Engine],
    tenant_settings: Settings,
) -> None:
    admin_engine, application_engine = tenant_database_engines
    with Session(admin_engine) as session:
        second_owner = session.get(Membership, tenant_seed.admin_membership_id)
        assert second_owner is not None
        second_owner.role = Role.OWNER
        session.commit()

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

    def demote(membership_id: UUID) -> str:
        with Session(application_engine) as session:
            set_request_context(
                session,
                user_id=principal.user_id,
                organization_id=principal.organization_id,
            )
            try:
                change_member_role(
                    principal,
                    membership_id,
                    Role.ADMIN,
                    session=session,
                )
            except TenancyError as error:
                return error.code
            return "updated"

    targets = [
        tenant_seed.owner_membership_a_id,
        tenant_seed.admin_membership_id,
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(demote, targets))

    assert sorted(outcomes) == ["last_owner_required", "updated"]
