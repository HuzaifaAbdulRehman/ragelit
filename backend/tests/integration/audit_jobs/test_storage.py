from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.tenancy.models import Membership
from app.tenancy.rls import set_request_context
from tests.api.tenant_support import TenantApiSeed


def test_audit_runs_force_rls(
    tenant_database_engines: tuple[Engine, Engine],
    tenant_seed: TenantApiSeed,
) -> None:
    from app.audit_jobs.models import AuditRun

    admin, app = tenant_database_engines
    with Session(admin, expire_on_commit=False) as session:
        requester = session.get(Membership, tenant_seed.owner_membership_a_id)
        assert requester is not None
        session.add(
            AuditRun(
                organization_id=tenant_seed.organization_a_id,
                requester_user_id=requester.user_id,
            )
        )
        session.commit()
    with Session(app) as session:
        assert session.scalars(select(AuditRun)).all() == []
        set_request_context(
            session,
            user_id=requester.user_id,
            organization_id=tenant_seed.organization_b_id,
        )
        assert session.scalars(select(AuditRun)).all() == []
        set_request_context(
            session,
            user_id=requester.user_id,
            organization_id=tenant_seed.organization_a_id,
        )
        assert len(session.scalars(select(AuditRun)).all()) == 1
    with admin.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                    "WHERE relname = 'audit_runs'"
                )
            ).scalar_one()
            is True
        )


def test_requester_cannot_reference_another_tenant(
    tenant_database_engines: tuple[Engine, Engine],
    tenant_seed: TenantApiSeed,
) -> None:
    from app.audit_jobs.models import AuditRun

    with Session(tenant_database_engines[0]) as session:
        outsider = session.get(Membership, tenant_seed.outsider_membership_b_id)
        assert outsider is not None
        session.add(
            AuditRun(
                organization_id=tenant_seed.organization_a_id,
                requester_user_id=outsider.user_id,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_concurrent_start_admits_one_run(
    tenant_database_engines: tuple[Engine, Engine],
    tenant_seed: TenantApiSeed,
) -> None:
    from app.audit_jobs.service import AuditJobError, enqueue_audit
    from app.tenancy.scope import RequestPrincipal

    with Session(tenant_database_engines[0]) as session:
        membership = session.get(Membership, tenant_seed.owner_membership_a_id)
        assert membership is not None
        principal = RequestPrincipal(
            user_id=membership.user_id,
            organization_id=membership.organization_id,
            session_id=uuid4(),
            membership_id=membership.id,
            role=membership.role,
        )

    def start() -> int:
        with Session(tenant_database_engines[1], expire_on_commit=False) as session:
            set_request_context(
                session,
                user_id=principal.user_id,
                organization_id=principal.organization_id,
            )
            try:
                enqueue_audit(principal, session=session)
            except AuditJobError as error:
                return error.status
            return 202

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(lambda _: start(), range(2))) == [202, 409]


def test_finished_run_requires_non_null_exit(
    tenant_database_engines: tuple[Engine, Engine], tenant_seed: TenantApiSeed
) -> None:
    from app.audit_jobs.models import AuditRun

    with Session(tenant_database_engines[0]) as session:
        requester = session.get(Membership, tenant_seed.owner_membership_a_id)
        assert requester is not None
        session.add(
            AuditRun(
                organization_id=tenant_seed.organization_a_id,
                requester_user_id=requester.user_id,
                state="finished",
                outcome="pass",
                finished_at=datetime.now(UTC),
                exit_code=None,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
