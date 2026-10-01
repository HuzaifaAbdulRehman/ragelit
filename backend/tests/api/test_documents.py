from collections.abc import Callable
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.api.tenant_support import TenantApiSeed


@pytest.fixture
def document_client(tenant_client: TestClient, tmp_path: Path) -> TestClient:
    state = cast(FastAPI, tenant_client.app).state
    state.settings = state.settings.model_copy(update={"data_dir": tmp_path})
    return tenant_client


def test_upload_queues_once_and_defaults_to_restricted(
    document_client: TestClient,
    tenant_seed: TenantApiSeed,
    login_headers: Callable[[str, str], dict[str, str]],
) -> None:
    headers = login_headers(tenant_seed.owner_email, tenant_seed.organization_a_slug)
    headers["Content-Type"] = "text/plain"
    body = f"Evidence for {uuid4()}".encode()
    first = document_client.post(
        "/api/v1/documents?filename=notes.txt", headers=headers, content=body
    )
    assert first.status_code == 202
    assert first.json()["visibility"] == "restricted"
    assert first.json()["state"] == "queued"
    second = document_client.post(
        "/api/v1/documents?filename=renamed.txt", headers=headers, content=body
    )
    assert second.status_code == 202
    assert second.json()["id"] == first.json()["id"]
    assert "storage_key" not in first.json()
    root = cast(FastAPI, document_client.app).state.settings.data_dir
    assert len(list(root.rglob("*.upload"))) == 1


def test_member_cannot_upload_or_view_ungranted_metadata(
    document_client: TestClient,
    tenant_seed: TenantApiSeed,
    login_headers: Callable[[str, str], dict[str, str]],
) -> None:
    owner = login_headers(tenant_seed.owner_email, tenant_seed.organization_a_slug)
    response = document_client.post(
        "/api/v1/documents?filename=private.txt",
        headers={**owner, "Content-Type": "text/plain"},
        content=uuid4().hex.encode(),
    )
    document_id = response.json()["id"]
    member = login_headers(tenant_seed.member_email, tenant_seed.organization_a_slug)
    denied = document_client.post(
        "/api/v1/documents?filename=a.txt",
        headers={**member, "Content-Type": "text/plain"},
        content=b"test",
    )
    assert denied.status_code == 403
    hidden = document_client.get(f"/api/v1/documents/{document_id}", headers=member)
    assert hidden.status_code == 404
    assert "private.txt" not in hidden.text


def test_foreign_document_is_hidden_from_other_tenant_owner(
    document_client: TestClient,
    tenant_seed: TenantApiSeed,
    login_headers: Callable[[str, str], dict[str, str]],
) -> None:
    owner = login_headers(tenant_seed.owner_email, tenant_seed.organization_a_slug)
    response = document_client.post(
        "/api/v1/documents?filename=confidential.txt",
        headers={**owner, "Content-Type": "text/plain"},
        content=uuid4().hex.encode(),
    )
    outsider = login_headers(
        tenant_seed.outsider_email, tenant_seed.organization_b_slug
    )
    hidden = document_client.get(
        f"/api/v1/documents/{response.json()['id']}", headers=outsider
    )
    assert hidden.status_code == 404
    assert "confidential" not in hidden.text


@pytest.mark.parametrize(
    "filename,media,body,status,code",
    [
        ("../../secret.txt", "text/plain", b"test", 422, "invalid_filename"),
        (
            "code.exe",
            "application/octet-stream",
            b"test",
            415,
            "unsupported_document_type",
        ),
        ("empty.txt", "text/plain", b"", 422, "empty_document"),
        ("a.txt", "text/plain", b"x" * 9, 413, "upload_too_large"),
    ],
)
def test_invalid_upload_has_no_stored_file(
    document_client: TestClient,
    tenant_seed: TenantApiSeed,
    login_headers: Callable[[str, str], dict[str, str]],
    filename: str,
    media: str,
    body: bytes,
    status: int,
    code: str,
) -> None:
    cast(FastAPI, document_client.app).state.settings.max_upload_bytes = 8
    headers = login_headers(tenant_seed.owner_email, tenant_seed.organization_a_slug)
    response = document_client.post(
        "/api/v1/documents",
        params={"filename": filename},
        headers={**headers, "Content-Type": media},
        content=body,
    )
    assert response.status_code == status
    assert response.json()["code"] == code
    root = cast(FastAPI, document_client.app).state.settings.data_dir
    assert list(root.rglob("*.upload")) == []
