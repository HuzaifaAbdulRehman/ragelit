from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from app.audit_jobs.contracts import CliOutcome
from app.audit_jobs.models import AuditRun
from app.tenancy.enums import Role
from app.tenancy.models import Membership
from app.tenancy.rls import set_request_context
from tests.api.tenant_support import TenantApiSeed
from tests.unit.audit_jobs.test_execution import configuration


def test_recovery_row_blocks_admission(
    tenant_database_engines: tuple[Engine, Engine],
    tenant_seed: TenantApiSeed,
) -> None:
    from app.audit_jobs.service import AuditJobError, enqueue_audit
    from app.tenancy.scope import RequestPrincipal

    run_id = queued(tenant_database_engines, tenant_seed)
    with Session(tenant_database_engines[0]) as session:
        run = session.get(AuditRun, run_id)
        membership = session.get(Membership, tenant_seed.owner_membership_a_id)
        assert run is not None and membership is not None
        run.state, run.outcome, run.exit_code = "recovery_required", "inconclusive", 2
        run.finished_at = datetime.now(UTC)
        principal = RequestPrincipal(
            membership.user_id,
            membership.organization_id,
            uuid4(),
            membership.id,
            membership.role,
        )
        session.commit()
    with Session(tenant_database_engines[1], expire_on_commit=False) as session:
        set_request_context(
            session,
            user_id=principal.user_id,
            organization_id=principal.organization_id,
        )
        with pytest.raises(AuditJobError) as error:
            enqueue_audit(principal, session=session)
        assert error.value.status == 409


def test_renewal_and_partial_result_are_persisted_atomically(
    tenant_database_engines: tuple[Engine, Engine],
    tenant_seed: TenantApiSeed,
    tmp_path: Path,
) -> None:
    import hashlib

    from app.audit_jobs.claims import claim_audit, finish_audit_claim, renew_audit_claim
    from tests.unit.audits.test_artifacts import partial_artifact

    run_id = queued(tenant_database_engines, tenant_seed)
    now = datetime.now(UTC)
    path = partial_artifact(tmp_path)
    content = path.read_bytes()
    outcome = CliOutcome(
        2,
        content.decode(),
        UUID(path.stem),
        hashlib.sha256(content).hexdigest(),
        "audit_incomplete",
    )
    with Session(tenant_database_engines[1]) as session:
        claim = claim_audit(tenant_seed.organization_a_id, session=session, now=now)
        assert claim is not None
        assert renew_audit_claim(
            claim, session=session, now=now + timedelta(seconds=30)
        )
        assert finish_audit_claim(
            claim, outcome, session=session, now=now + timedelta(seconds=70)
        )
        assert not finish_audit_claim(
            claim, CliOutcome(0), session=session, now=now + timedelta(seconds=71)
        )
    with Session(tenant_database_engines[0]) as session:
        run = session.get(AuditRun, run_id)
        assert (
            run is not None
            and run.state == "finished"
            and run.outcome == "inconclusive"
        )
        assert (
            run.report_id == outcome.report_id
            and run.report_content == outcome.report_content
        )


def queued(engines: tuple[Engine, Engine], seed: TenantApiSeed) -> UUID:
    with Session(engines[0], expire_on_commit=False) as session:
        membership = session.get(Membership, seed.owner_membership_a_id)
        assert membership is not None
        run = AuditRun(
            organization_id=seed.organization_a_id, requester_user_id=membership.user_id
        )
        session.add(run)
        session.commit()
        return run.id


def test_stale_claim_cannot_publish(
    tenant_database_engines: tuple[Engine, Engine], tenant_seed: TenantApiSeed
) -> None:
    from app.audit_jobs.claims import claim_audit, finish_audit_claim, renew_audit_claim

    run_id = queued(tenant_database_engines, tenant_seed)
    now = datetime.now(UTC)
    with Session(tenant_database_engines[1]) as session:
        claim = claim_audit(tenant_seed.organization_a_id, session=session, now=now)
        assert claim is not None and claim.request_id == run_id
        assert not finish_audit_claim(
            replace(claim, claim_id=uuid4()), CliOutcome(0), session=session, now=now
        )
        assert not finish_audit_claim(
            claim, CliOutcome(0), session=session, now=now + timedelta(seconds=61)
        )
        assert not renew_audit_claim(
            claim, session=session, now=now + timedelta(seconds=61)
        )
    with Session(tenant_database_engines[0]) as session:
        run = session.get(AuditRun, run_id)
        assert run is not None and run.state == "running" and run.outcome == "unknown"


def test_expired_lease_requires_explicit_recovery(
    tenant_database_engines: tuple[Engine, Engine], tenant_seed: TenantApiSeed
) -> None:
    from app.audit_jobs.claims import claim_audit, resolve_interrupted_audit
    from app.audit_jobs.service import AuditJobError

    run_id = queued(tenant_database_engines, tenant_seed)
    with Session(tenant_database_engines[1]) as session:
        claim = claim_audit(
            tenant_seed.organization_a_id,
            session=session,
            now=datetime.now(UTC) - timedelta(seconds=61),
        )
        assert claim is not None
        assert claim_audit(tenant_seed.organization_a_id, session=session) is None
        for confirmed, org in (
            (False, tenant_seed.organization_a_id),
            (True, tenant_seed.organization_b_id),
        ):
            with pytest.raises(AuditJobError):
                resolve_interrupted_audit(
                    run_id, org, session=session, worker_stopped_confirmed=confirmed
                )
            session.rollback()
        resolve_interrupted_audit(
            run_id,
            tenant_seed.organization_a_id,
            session=session,
            worker_stopped_confirmed=True,
        )
    with Session(tenant_database_engines[0]) as session:
        run = session.get(AuditRun, run_id)
        assert run is not None and run.state == "finished"
        assert run.outcome == "inconclusive" and run.exit_code == 2


@pytest.mark.parametrize("change", ["user", "membership", "role"])
def test_revoked_requester_never_executes(
    tenant_database_engines: tuple[Engine, Engine],
    tenant_seed: TenantApiSeed,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    from app.identity.models import User
    from app.workers import audit

    run_id = queued(tenant_database_engines, tenant_seed)
    with Session(tenant_database_engines[0]) as session:
        membership = session.get(Membership, tenant_seed.owner_membership_a_id)
        assert membership is not None
        if change == "role":
            membership.role = Role.MEMBER
        elif change == "membership":
            membership.is_active = False
        else:
            user = session.get(User, membership.user_id)
            assert user is not None
            user.is_active = False
        session.commit()

    def forbidden(*args: object, **kwargs: object) -> CliOutcome:
        raise AssertionError("revoked requester reached execution")

    monkeypatch.setattr(audit, "run_cli", forbidden)
    audit.run_once(
        tenant_seed.organization_a_id,
        session_factory=sessionmaker(tenant_database_engines[1]),
        configuration=configuration(tmp_path),
    )
    with Session(tenant_database_engines[0]) as session:
        run = session.get(AuditRun, run_id)
        assert run is not None and run.state == "finished"
        assert run.error_code == "audit_requester_revoked" and run.exit_code == 2


def test_global_lock_serializes_healthy_workers(
    tenant_database_engines: tuple[Engine, Engine],
    tenant_seed: TenantApiSeed,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workers import audit

    queued(tenant_database_engines, tenant_seed)
    with Session(tenant_database_engines[0]) as session:
        member = session.get(Membership, tenant_seed.outsider_membership_b_id)
        assert member is not None
        member.role = Role.OWNER
        session.add(
            AuditRun(
                organization_id=tenant_seed.organization_b_id,
                requester_user_id=member.user_id,
            )
        )
        session.commit()
    entered, release = Event(), Event()

    def execute(*args: object, **kwargs: object) -> CliOutcome:
        entered.set()
        assert release.wait(10)
        return CliOutcome(2, error_code="audit_incomplete")

    monkeypatch.setattr(audit, "run_cli", execute)
    factory = sessionmaker(tenant_database_engines[1])
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            audit.run_once,
            tenant_seed.organization_a_id,
            session_factory=factory,
            configuration=configuration(tmp_path),
        )
        try:
            assert entered.wait(10)
            assert not audit.run_once(
                tenant_seed.organization_b_id,
                session_factory=factory,
                configuration=configuration(tmp_path),
            )
        finally:
            release.set()
        assert first.result(timeout=10)
    with Session(tenant_database_engines[0]) as session:
        assert (
            session.scalar(
                select(AuditRun.state).where(
                    AuditRun.organization_id == tenant_seed.organization_b_id
                )
            )
            == "queued"
        )


def test_application_role_cannot_insert_foreign_run(
    tenant_database_engines: tuple[Engine, Engine], tenant_seed: TenantApiSeed
) -> None:
    with Session(tenant_database_engines[0]) as session:
        owner = session.get(Membership, tenant_seed.owner_membership_a_id)
        assert owner is not None
        user_id = owner.user_id
    with Session(tenant_database_engines[1]) as session:
        set_request_context(
            session, user_id=user_id, organization_id=tenant_seed.organization_a_id
        )
        session.add(
            AuditRun(
                organization_id=tenant_seed.organization_b_id, requester_user_id=user_id
            )
        )
        with pytest.raises(ProgrammingError):
            session.commit()
