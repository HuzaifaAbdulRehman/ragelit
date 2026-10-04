from typing import Annotated, Any, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import DatabaseSession, require_action
from app.audit_jobs.schemas import (
    AuditRunCreate,
    AuditRunDetail,
    AuditRunList,
    AuditRunSummary,
)
from app.audit_jobs.service import (
    AuditJobError,
    download_report,
    enqueue_audit,
    get_audit,
    list_audits,
    summarize_run,
)
from app.audits.reports import AuditReport
from app.core.problems import ProblemDetail, ProblemException
from app.tenancy.policy import Action
from app.tenancy.scope import RequestPrincipal

router = APIRouter(prefix="/audits", tags=["audits"])
AuditRunner = Annotated[RequestPrincipal, Depends(require_action(Action.AUDITS_RUN))]
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0)]
RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": ProblemDetail} for status in (401, 403, 404, 409, 503)
}


def _raise_error(error: AuditJobError) -> NoReturn:
    details = {
        "action_forbidden": "This action is not permitted.",
        "resource_not_found": "The requested resource was not found.",
        "audit_already_outstanding": "An audit already needs completion or recovery.",
        "audit_report_not_ready": "The audit does not have a report yet.",
        "audit_report_invalid": "The saved audit report could not be validated.",
    }
    titles = {
        403: "Forbidden",
        404: "Not Found",
        409: "Conflict",
        503: "Service Unavailable",
    }
    raise ProblemException(
        status=error.status,
        code=error.code,
        title=titles[error.status],
        detail=details[error.code],
    ) from error


@router.post("", status_code=202, responses=RESPONSES)
def start_audit_route(
    command: AuditRunCreate, principal: AuditRunner, session: DatabaseSession
) -> AuditRunSummary:
    try:
        return summarize_run(enqueue_audit(principal, session=session))
    except AuditJobError as error:
        _raise_error(error)


@router.get("", responses=RESPONSES)
def list_audits_route(
    principal: AuditRunner,
    session: DatabaseSession,
    limit: PageLimit = 20,
    offset: PageOffset = 0,
) -> AuditRunList:
    return list_audits(principal, session=session, limit=limit, offset=offset)


@router.get("/{run_id}", responses=RESPONSES)
def audit_detail_route(
    run_id: UUID, principal: AuditRunner, session: DatabaseSession
) -> AuditRunDetail:
    try:
        run = get_audit(run_id, principal, session=session)
        report = (
            AuditReport.model_validate_json(download_report(run))
            if run.report_id is not None
            else None
        )
        return AuditRunDetail(**summarize_run(run).model_dump(), report=report)
    except AuditJobError as error:
        _raise_error(error)


@router.get("/{run_id}/report.json", responses=RESPONSES, response_class=Response)
def audit_download_route(
    run_id: UUID, principal: AuditRunner, session: DatabaseSession
) -> Response:
    try:
        run = get_audit(run_id, principal, session=session)
        content = download_report(run)
    except AuditJobError as error:
        _raise_error(error)
    return Response(
        content,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{run.report_id}.json"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
