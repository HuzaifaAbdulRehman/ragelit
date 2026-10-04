import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.tenancy.enums import Role
from app.tenancy.models import Membership
from tests.api.tenant_support import TenantApiSeed
from tests.unit.audits.test_artifacts import partial_artifact


@pytest.mark.parametrize("actor", ["owner", "admin", "auditor", "member"])
def test_every_audit_route_requires_current_privileged_role(
    tenant_seed: TenantApiSeed,
    tenant_client: TestClient,
    login_headers: Callable[[str, str], dict[str, str]],
    actor: str,
) -> None:
    headers = login_headers(
        getattr(tenant_seed, f"{actor}_email"), tenant_seed.organization_a_slug
    )
    created = tenant_client.post("/api/v1/audits", headers=headers, json={})
    allowed = actor != "member"
    assert created.status_code == (202 if allowed else 403)
    run_id = created.json()["id"] if allowed else str(uuid4())
    assert tenant_client.get("/api/v1/audits", headers=headers).status_code == (
        200 if allowed else 403
    )
    detail = tenant_client.get(f"/api/v1/audits/{run_id}", headers=headers)
    assert detail.status_code == (200 if allowed else 403)
    download = tenant_client.get(
        f"/api/v1/audits/{run_id}/report.json", headers=headers
    )
    assert download.status_code == (409 if allowed else 403)
    if allowed:
        assert created.json()["state"] == "queued"
        assert created.json()["outcome"] == "unknown"
        assert detail.json()["report"] is None
        assert "claim_id" not in detail.json()
        assert download.json()["code"] == "audit_report_not_ready"


@pytest.mark.parametrize(
    "field",
    [
        "profile",
        "organization_id",
        "target",
        "path",
        "credentials",
        "case",
        "arguments",
    ],
)
def test_start_rejects_execution_fields(
    tenant_seed: TenantApiSeed,
    tenant_client: TestClient,
    login_headers: Callable[[str, str], dict[str, str]],
    field: str,
) -> None:
    headers = login_headers(tenant_seed.owner_email, tenant_seed.organization_a_slug)
    response = tenant_client.post(
        "/api/v1/audits", headers=headers, json={field: "SyntheticClientMarker"}
    )
    assert response.status_code == 422
    assert "SyntheticClientMarker" not in response.text
    assert tenant_client.get("/api/v1/audits", headers=headers).json()["total"] == 0


def test_foreign_run_is_not_found(
    tenant_seed: TenantApiSeed,
    tenant_client: TestClient,
    tenant_database_engines: tuple[Engine, Engine],
    login_headers: Callable[[str, str], dict[str, str]],
) -> None:
    with Session(tenant_database_engines[0]) as session:
        membership = session.get(Membership, tenant_seed.owner_membership_b_id)
        assert membership is not None
        membership.role = Role.ADMIN
        session.commit()
    headers_a = login_headers(tenant_seed.owner_email, tenant_seed.organization_a_slug)
    headers_b = login_headers(tenant_seed.owner_email, tenant_seed.organization_b_slug)
    run_id = tenant_client.post("/api/v1/audits", headers=headers_a, json={}).json()[
        "id"
    ]
    for suffix in ("", "/report.json"):
        response = tenant_client.get(
            f"/api/v1/audits/{run_id}{suffix}", headers=headers_b
        )
        assert response.status_code == 404
    assert tenant_client.get("/api/v1/audits", headers=headers_b).json()["total"] == 0


def test_existing_token_loses_audit_access_after_role_change(
    tenant_seed: TenantApiSeed,
    tenant_client: TestClient,
    tenant_database_engines: tuple[Engine, Engine],
    login_headers: Callable[[str, str], dict[str, str]],
) -> None:
    headers = login_headers(tenant_seed.admin_email, tenant_seed.organization_a_slug)
    run_id = tenant_client.post("/api/v1/audits", headers=headers, json={}).json()["id"]
    with Session(tenant_database_engines[0]) as session:
        membership = session.get(Membership, tenant_seed.admin_membership_id)
        assert membership is not None
        membership.role = Role.MEMBER
        session.commit()
    for path in (
        "/api/v1/audits",
        f"/api/v1/audits/{run_id}",
        f"/api/v1/audits/{run_id}/report.json",
    ):
        assert tenant_client.get(path, headers=headers).status_code == 403
    assert (
        tenant_client.post("/api/v1/audits", headers=headers, json={}).status_code
        == 403
    )


def test_deactivated_membership_cannot_read_or_start(
    tenant_seed: TenantApiSeed,
    tenant_client: TestClient,
    tenant_database_engines: tuple[Engine, Engine],
    login_headers: Callable[[str, str], dict[str, str]],
) -> None:
    headers = login_headers(tenant_seed.admin_email, tenant_seed.organization_a_slug)
    run_id = tenant_client.post("/api/v1/audits", headers=headers, json={}).json()["id"]
    with Session(tenant_database_engines[0]) as session:
        membership = session.get(Membership, tenant_seed.admin_membership_id)
        assert membership is not None
        membership.is_active = False
        session.commit()
    for path in (
        "/api/v1/audits",
        f"/api/v1/audits/{run_id}",
        f"/api/v1/audits/{run_id}/report.json",
    ):
        assert tenant_client.get(path, headers=headers).status_code == 401
    assert (
        tenant_client.post("/api/v1/audits", headers=headers, json={}).status_code
        == 401
    )


def test_download_preserves_utf8_bytes(
    tenant_seed: TenantApiSeed,
    tenant_client: TestClient,
    tenant_database_engines: tuple[Engine, Engine],
    login_headers: Callable[[str, str], dict[str, str]],
    tmp_path: Path,
) -> None:
    from app.audit_jobs.models import AuditRun

    headers = login_headers(tenant_seed.owner_email, tenant_seed.organization_a_slug)
    run_id = UUID(
        tenant_client.post("/api/v1/audits", headers=headers, json={}).json()["id"]
    )
    path = partial_artifact(tmp_path)
    content = (
        json.dumps(json.loads(path.read_text()), indent=2)
        .replace('"safe"', '"\\u0073afe"')
        .encode("utf-8")
    )
    digest = hashlib.sha256(content).hexdigest()
    report_id = UUID(json.loads(content)["run_id"])
    with Session(tenant_database_engines[0]) as session:
        run = session.get(AuditRun, run_id)
        assert run is not None
        run.state, run.outcome, run.exit_code = "finished", "inconclusive", 2
        run.finished_at = datetime.now(UTC)
        run.report_content = content.decode("utf-8")
        run.report_id, run.report_sha256 = report_id, digest
        session.commit()
    for actor in ("owner", "admin", "auditor"):
        actor_headers = login_headers(
            getattr(tenant_seed, f"{actor}_email"), tenant_seed.organization_a_slug
        )
        response = tenant_client.get(
            f"/api/v1/audits/{run_id}/report.json", headers=actor_headers
        )
        assert response.status_code == 200
        assert response.content == content
        assert hashlib.sha256(response.content).hexdigest() == digest
        assert response.headers["content-disposition"].endswith(f'{report_id}.json"')
    with Session(tenant_database_engines[0]) as session:
        run = session.get(AuditRun, run_id)
        assert run is not None
        run.report_sha256 = "0" * 64
        session.commit()
    for suffix in ("", "/report.json"):
        response = tenant_client.get(
            f"/api/v1/audits/{run_id}{suffix}", headers=headers
        )
        assert response.status_code == 503
        assert response.json()["code"] == "audit_report_invalid"


def test_expired_running_lease_projects_recovery_without_mutating(
    tenant_seed: TenantApiSeed,
    tenant_client: TestClient,
    tenant_database_engines: tuple[Engine, Engine],
    login_headers: Callable[[str, str], dict[str, str]],
) -> None:
    from app.audit_jobs.models import AuditRun

    headers = login_headers(tenant_seed.owner_email, tenant_seed.organization_a_slug)
    run_id = UUID(
        tenant_client.post("/api/v1/audits", headers=headers, json={}).json()["id"]
    )
    with Session(tenant_database_engines[0]) as session:
        run = session.get(AuditRun, run_id)
        assert run is not None
        run.state, run.claim_id = "running", uuid4()
        run.started_at = datetime.now(UTC) - timedelta(minutes=2)
        run.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    response = tenant_client.get(f"/api/v1/audits/{run_id}", headers=headers)
    assert response.json()["state"] == "recovery_required"
    assert response.json()["outcome"] == "inconclusive"
    assert (
        tenant_client.post("/api/v1/audits", headers=headers, json={}).status_code
        == 409
    )
    with Session(tenant_database_engines[0]) as session:
        assert (
            session.scalar(select(AuditRun.state).where(AuditRun.id == run_id))
            == "running"
        )
