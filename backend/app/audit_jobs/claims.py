from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.audit_jobs.contracts import AuditClaim, CliOutcome
from app.audit_jobs.models import AuditRun
from app.audit_jobs.service import AuditJobError
from app.identity.models import User
from app.tenancy.models import Membership
from app.tenancy.policy import Action, role_allows
from app.tenancy.rls import set_request_context

LEASE_SECONDS = 60
_NO_USER = UUID(int=0)


def _context(session: Session, org_id: UUID, user_id: UUID = _NO_USER) -> None:
    set_request_context(session, user_id=user_id, organization_id=org_id)


def requester_can_run(user_id: UUID, org_id: UUID, *, session: Session) -> bool:
    _context(session, org_id, user_id)
    role = session.scalar(
        select(Membership.role)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.user_id == user_id,
            Membership.organization_id == org_id,
            Membership.is_active.is_(True),
            User.is_active.is_(True),
        )
    )
    return role is not None and role_allows(role, Action.AUDITS_RUN)


def claim_audit(
    org_id: UUID, *, session: Session, now: datetime | None = None
) -> AuditClaim | None:
    _context(session, org_id)
    current = now or datetime.now(UTC)
    run = session.scalar(
        select(AuditRun)
        .where(
            AuditRun.organization_id == org_id,
            AuditRun.state.in_(("queued", "running", "recovery_required")),
        )
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if run is None or run.state == "recovery_required":
        session.rollback()
        return None
    if run.state == "running":
        if run.lease_until is not None and run.lease_until <= current:
            run.state, run.outcome, run.exit_code = (
                "recovery_required",
                "inconclusive",
                2,
            )
            run.finished_at, run.error_code = current, "audit_interrupted"
            session.commit()
        else:
            session.rollback()
        return None
    if not requester_can_run(run.requester_user_id, org_id, session=session):
        run.state, run.outcome, run.exit_code = "finished", "inconclusive", 2
        run.finished_at, run.error_code = current, "audit_requester_revoked"
        session.commit()
        return None
    run.state, run.claim_id = "running", uuid4()
    run.started_at, run.lease_until = (
        current,
        current + timedelta(seconds=LEASE_SECONDS),
    )
    claim = AuditClaim(
        run.id, org_id, run.requester_user_id, run.claim_id, run.lease_until
    )
    session.commit()
    return claim


def _claim_predicate(
    claim: AuditClaim, current: datetime
) -> tuple[ColumnElement[bool], ...]:
    return (
        AuditRun.id == claim.request_id,
        AuditRun.organization_id == claim.organization_id,
        AuditRun.requester_user_id == claim.requester_user_id,
        AuditRun.claim_id == claim.claim_id,
        AuditRun.state == "running",
        AuditRun.lease_until > current,
    )


def renew_audit_claim(
    claim: AuditClaim, *, session: Session, now: datetime | None = None
) -> bool:
    current = now or datetime.now(UTC)
    _context(session, claim.organization_id, claim.requester_user_id)
    updated = session.execute(
        update(AuditRun)
        .where(*_claim_predicate(claim, current))
        .values(
            lease_until=current + timedelta(seconds=LEASE_SECONDS),
        )
        .returning(AuditRun.id)
    ).scalar_one_or_none()
    session.commit()
    return updated is not None


def finish_audit_claim(
    claim: AuditClaim,
    outcome: CliOutcome,
    *,
    session: Session,
    now: datetime | None = None,
) -> bool:
    current = now or datetime.now(UTC)
    _context(session, claim.organization_id, claim.requester_user_id)
    if outcome.exit_code != 2 and outcome.report_content is None:
        outcome = CliOutcome(2, error_code="audit_artifact_invalid")
    updated = session.execute(
        update(AuditRun)
        .where(*_claim_predicate(claim, current))
        .values(
            state="recovery_required" if outcome.recovery_required else "finished",
            outcome={0: "pass", 1: "fail", 2: "inconclusive"}[outcome.exit_code],
            exit_code=outcome.exit_code,
            finished_at=current,
            error_code=outcome.error_code,
            report_content=outcome.report_content,
            report_id=outcome.report_id,
            report_sha256=outcome.report_sha256,
        )
        .returning(AuditRun.id)
    ).scalar_one_or_none()
    session.commit()
    return updated is not None


def mark_recovery_required(
    claim: AuditClaim, *, session: Session, now: datetime | None = None
) -> bool:
    _context(session, claim.organization_id, claim.requester_user_id)
    current = now or datetime.now(UTC)
    updated = session.execute(
        update(AuditRun)
        .where(
            AuditRun.id == claim.request_id,
            AuditRun.organization_id == claim.organization_id,
            AuditRun.requester_user_id == claim.requester_user_id,
            AuditRun.claim_id == claim.claim_id,
            AuditRun.state == "running",
        )
        .values(
            state="recovery_required",
            outcome="inconclusive",
            exit_code=2,
            finished_at=current,
            error_code="audit_interrupted",
        )
        .returning(AuditRun.id)
    ).scalar_one_or_none()
    session.commit()
    return updated is not None


def resolve_interrupted_audit(
    run_id: UUID, org_id: UUID, *, session: Session, worker_stopped_confirmed: bool
) -> None:
    if not worker_stopped_confirmed:
        raise AuditJobError("audit_recovery_confirmation_required", 409)
    _context(session, org_id)
    run = session.scalar(
        select(AuditRun)
        .where(
            AuditRun.id == run_id,
            AuditRun.organization_id == org_id,
        )
        .with_for_update()
    )
    if run is None:
        raise AuditJobError("resource_not_found", 404)
    current = datetime.now(UTC)
    expired = (
        run.state == "running"
        and run.lease_until is not None
        and run.lease_until <= current
    )
    if run.state != "recovery_required" and not expired:
        raise AuditJobError("audit_recovery_not_required", 409)
    run.state, run.outcome, run.exit_code = "finished", "inconclusive", 2
    run.finished_at, run.error_code = current, "audit_interrupted"
    run.claim_id, run.lease_until = None, None
    session.commit()
