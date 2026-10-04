import hashlib
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, defer

from app.audit_jobs.models import AuditRun
from app.audit_jobs.schemas import AuditRunList, AuditRunSummary, JobOutcome, JobState
from app.audits.reports import AuditReport
from app.tenancy.policy import Action, role_allows
from app.tenancy.scope import RequestPrincipal


class AuditJobError(Exception):
    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.code, self.status = code, status


def _authorize(principal: RequestPrincipal) -> None:
    if not role_allows(principal.role, Action.AUDITS_RUN):
        raise AuditJobError("action_forbidden", 403)


def enqueue_audit(principal: RequestPrincipal, *, session: Session) -> AuditRun:
    _authorize(principal)
    run = AuditRun(
        organization_id=principal.organization_id, requester_user_id=principal.user_id
    )
    try:
        with session.begin_nested():
            session.add(run)
            session.flush()
    except IntegrityError as error:
        diagnostic = getattr(error.orig, "diag", None)
        if (
            getattr(diagnostic, "constraint_name", None)
            == "uq_audit_runs_outstanding_organization"
        ):
            raise AuditJobError("audit_already_outstanding", 409) from error
        raise
    session.commit()
    return run


def list_audits(
    principal: RequestPrincipal, *, session: Session, limit: int = 20, offset: int = 0
) -> AuditRunList:
    _authorize(principal)
    predicate = AuditRun.organization_id == principal.organization_id
    total = (
        session.scalar(select(func.count()).select_from(AuditRun).where(predicate)) or 0
    )
    rows = session.scalars(
        select(AuditRun)
        .where(predicate)
        .options(defer(AuditRun.report_content))
        .order_by(AuditRun.created_at.desc(), AuditRun.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return AuditRunList(
        items=[summarize_run(run) for run in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def get_audit(
    run_id: UUID, principal: RequestPrincipal, *, session: Session
) -> AuditRun:
    _authorize(principal)
    run = session.scalar(
        select(AuditRun).where(
            AuditRun.id == run_id, AuditRun.organization_id == principal.organization_id
        )
    )
    if run is None:
        raise AuditJobError("resource_not_found", 404)
    return run


def summarize_run(run: AuditRun, *, now: datetime | None = None) -> AuditRunSummary:
    expired = (
        run.state == "running"
        and run.lease_until is not None
        and run.lease_until <= (now or datetime.now(UTC))
    )
    return AuditRunSummary(
        id=run.id,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        state="recovery_required" if expired else cast(JobState, run.state),
        outcome="inconclusive" if expired else cast(JobOutcome, run.outcome),
        exit_code=2 if expired else cast(Literal[0, 1, 2] | None, run.exit_code),
        error_code="audit_interrupted" if expired else run.error_code,
        report_available=run.report_id is not None,
        report_id=run.report_id,
        report_sha256=run.report_sha256,
    )


def download_report(run: AuditRun) -> bytes:
    if run.report_content is None:
        raise AuditJobError("audit_report_not_ready", 409)
    try:
        content = run.report_content.encode("utf-8")
        if (
            len(content) > 8 * 1024 * 1024
            or hashlib.sha256(content).hexdigest() != run.report_sha256
            or any(marker in content for marker in (b"AUDITCANARY", b"Bearer "))
        ):
            raise ValueError("invalid_report")
        report = AuditReport.model_validate_json(content)
        if (
            report.run_id != run.report_id
            or report.metadata.profile != "safe"
            or report.exit_code != run.exit_code
        ):
            raise ValueError("invalid_report")
    except (UnicodeError, ValueError, ValidationError) as error:
        raise AuditJobError("audit_report_invalid", 503) from error
    return content
