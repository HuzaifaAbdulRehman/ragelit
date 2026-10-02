from collections.abc import Callable
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.documents.models import Document, DocumentGrant
from app.tenancy.models import Membership
from tests.api.tenant_support import TenantApiSeed


@pytest.fixture
def document_client(tenant_client: TestClient, tmp_path: Path) -> TestClient:
    state = cast(FastAPI, tenant_client.app).state
    state.settings = state.settings.model_copy(update={"data_dir": tmp_path})
    return tenant_client


@pytest.fixture
def restricted_document(
    document_client: TestClient,
    tenant_seed: TenantApiSeed,
    login_headers: Callable[[str, str], dict[str, str]],
) -> UUID:
    headers = login_headers(tenant_seed.owner_email, tenant_seed.organization_a_slug)
    response = document_client.post(
        "/api/v1/documents?filename=private.txt",
        headers={**headers, "Content-Type": "application/octet-stream"},
        content=uuid4().hex.encode(),
    )
    assert response.status_code == 202
    return UUID(response.json()["id"])


def test_manager_reads_existing_grants_without_document_read_access(
    document_client: TestClient,
    restricted_document: UUID,
    tenant_seed: TenantApiSeed,
    tenant_database_engines: tuple[Engine, Engine],
    login_headers: Callable[[str, str], dict[str, str]],
) -> None:
    with Session(tenant_database_engines[0]) as session:
        membership = session.get(Membership, tenant_seed.member_membership_id)
        assert membership is not None
        expected_user_id = membership.user_id
        session.add_all(
            [
                DocumentGrant(
                    organization_id=tenant_seed.organization_a_id,
                    document_id=restricted_document,
                    user_id=expected_user_id,
                ),
                DocumentGrant(
                    organization_id=tenant_seed.organization_a_id,
                    document_id=restricted_document,
                    group_id=tenant_seed.group_a_id,
                ),
            ]
        )
        session.commit()
    admin = login_headers(tenant_seed.admin_email, tenant_seed.organization_a_slug)
    allowed = document_client.get(
        f"/api/v1/documents/{restricted_document}/access", headers=admin
    )
    assert allowed.status_code == 200
    assert allowed.json() == {
        "visibility": "restricted",
        "user_ids": [str(expected_user_id)],
        "group_ids": [str(tenant_seed.group_a_id)],
    }
    assert "private.txt" not in allowed.text


@pytest.mark.parametrize("actor", ["member", "auditor"])
def test_member_cannot_read_grant_targets(
    document_client: TestClient,
    restricted_document: UUID,
    tenant_seed: TenantApiSeed,
    login_headers: Callable[[str, str], dict[str, str]],
    actor: str,
) -> None:
    email = tenant_seed.member_email if actor == "member" else tenant_seed.auditor_email
    member = document_client.get(
        f"/api/v1/documents/{restricted_document}/access",
        headers=login_headers(email, tenant_seed.organization_a_slug),
    )
    assert member.status_code == 403


def test_foreign_and_deleted_document_access_is_not_found(
    document_client: TestClient,
    restricted_document: UUID,
    tenant_seed: TenantApiSeed,
    tenant_database_engines: tuple[Engine, Engine],
    login_headers: Callable[[str, str], dict[str, str]],
) -> None:
    foreign = document_client.get(
        f"/api/v1/documents/{restricted_document}/access",
        headers=login_headers(
            tenant_seed.outsider_email, tenant_seed.organization_b_slug
        ),
    )
    assert foreign.status_code == 404
    with Session(tenant_database_engines[0]) as session:
        document = session.get(Document, restricted_document)
        assert document is not None
        document.state = "deleted"
        session.commit()
    deleted = document_client.get(
        f"/api/v1/documents/{restricted_document}/access",
        headers=login_headers(tenant_seed.owner_email, tenant_seed.organization_a_slug),
    )
    assert deleted.status_code == 404
    assert "private.txt" not in deleted.text
