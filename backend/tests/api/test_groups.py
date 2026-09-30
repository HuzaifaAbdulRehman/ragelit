from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from tests.api.tenant_support import TenantApiSeed


def test_owner_can_manage_group_lifecycle_and_membership(
    tenant_seed: TenantApiSeed,
    tenant_client: TestClient,
    login_headers: Callable[[str, str], dict[str, str]],
) -> None:
    headers = login_headers(tenant_seed.owner_email, tenant_seed.organization_a_slug)
    base = f"/api/v1/organizations/{tenant_seed.organization_a_id}/groups"

    created = tenant_client.post(base, headers=headers, json={"name": "Reviewers"})
    assert created.status_code == 201
    group_id = created.json()["id"]
    renamed = tenant_client.patch(
        f"{base}/{group_id}",
        headers=headers,
        json={"name": "Security reviewers"},
    )
    added = tenant_client.post(
        f"{base}/{group_id}/members/{tenant_seed.member_membership_id}",
        headers=headers,
    )
    removed = tenant_client.delete(
        f"{base}/{group_id}/members/{tenant_seed.member_membership_id}",
        headers=headers,
    )
    deleted = tenant_client.delete(f"{base}/{group_id}", headers=headers)

    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Security reviewers"
    assert added.status_code == 204
    assert removed.status_code == 204
    assert deleted.status_code == 204


def test_admin_can_create_and_list_groups(
    tenant_seed: TenantApiSeed,
    tenant_client: TestClient,
    login_headers: Callable[[str, str], dict[str, str]],
) -> None:
    headers = login_headers(tenant_seed.admin_email, tenant_seed.organization_a_slug)
    base = f"/api/v1/organizations/{tenant_seed.organization_a_id}/groups"

    created = tenant_client.post(base, headers=headers, json={"name": "Editors"})
    listed = tenant_client.get(f"{base}?limit=10&offset=0", headers=headers)

    assert created.status_code == 201
    assert listed.status_code == 200
    assert created.json()["id"] in {item["id"] for item in listed.json()["items"]}


@pytest.mark.parametrize("actor", ["auditor", "member"])
def test_auditor_and_member_cannot_manage_groups(
    tenant_seed: TenantApiSeed,
    tenant_client: TestClient,
    login_headers: Callable[[str, str], dict[str, str]],
    actor: str,
) -> None:
    email = (
        tenant_seed.auditor_email if actor == "auditor" else tenant_seed.member_email
    )
    headers = login_headers(email, tenant_seed.organization_a_slug)

    response = tenant_client.post(
        f"/api/v1/organizations/{tenant_seed.organization_a_id}/groups",
        headers=headers,
        json={"name": "Blocked"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "action_forbidden"


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", "list", None),
        ("PATCH", "rename", {"name": "Stolen"}),
        ("DELETE", "delete", None),
        ("POST", "add_member", None),
        ("DELETE", "remove_member", None),
    ],
)
def test_group_routes_hide_other_tenants(
    tenant_seed: TenantApiSeed,
    tenant_client: TestClient,
    login_headers: Callable[[str, str], dict[str, str]],
    method: str,
    path: str,
    payload: dict[str, object] | None,
) -> None:
    headers = login_headers(tenant_seed.owner_email, tenant_seed.organization_a_slug)
    base = f"/api/v1/organizations/{tenant_seed.organization_b_id}/groups"
    if path == "list":
        url = base
    elif path in {"rename", "delete"}:
        url = f"{base}/{tenant_seed.group_b_id}"
    else:
        url = (
            f"{base}/{tenant_seed.group_b_id}"
            f"/members/{tenant_seed.outsider_membership_b_id}"
        )

    response = tenant_client.request(method, url, headers=headers, json=payload)

    body = response.text
    assert response.status_code == 404
    assert response.json()["code"] == "resource_not_found"
    assert tenant_seed.organization_b_name not in body
    assert tenant_seed.group_b_name not in body
    assert str(tenant_seed.organization_b_id) not in body
    assert str(tenant_seed.group_b_id) not in body
