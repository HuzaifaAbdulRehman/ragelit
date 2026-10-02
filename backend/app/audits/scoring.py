from typing import Literal

from app.audits.contracts import (
    AuditCase,
    AuditCaseResult,
    AuditObservation,
    Boundary,
    BoundaryEvidence,
    Reason,
    Terminal,
)

_DELIVERY = {Boundary.OUTPUT_DELIVERED, Boundary.CITATIONS_DELIVERED}
_GENERATION = _DELIVERY | {
    Boundary.OUTPUT_CANDIDATE,
    Boundary.CITATIONS_CANDIDATE,
}


def _not_reached_is_proven(
    stage: BoundaryEvidence, observation: AuditObservation
) -> bool:
    if observation.terminal in {
        Terminal.AUTHENTICATION_DENIED,
        Terminal.VALIDATION_DENIED,
    }:
        return observation.http_status in {401, 403, 422}
    if observation.terminal == Terminal.ABSTAINED:
        return observation.http_status == 200 and stage.boundary in _GENERATION
    if observation.terminal == Terminal.CITATIONS_REJECTED:
        return observation.http_status == 502 and stage.boundary in _DELIVERY
    if observation.terminal == Terminal.RETRIEVAL_REJECTED:
        return (
            observation.http_status == 503 and stage.boundary != Boundary.RETRIEVAL_RAW
        )
    return False


def _coverage_complete(
    case: AuditCase, observation: AuditObservation, first_exposure: Boundary | None
) -> bool:
    contained_exposure = (
        first_exposure == Boundary.RETRIEVAL_RAW
        and observation.terminal == Terminal.RETRIEVAL_REJECTED
        and observation.http_status == 503
    )
    if (
        case.id != observation.case_id
        or (observation.http_status != case.expected_status and not contained_exposure)
        or observation.terminal in {Terminal.RUNTIME_FAILED, Terminal.OBSERVER_FAILED}
    ):
        return False
    if observation.scope_hash is None and observation.terminal not in {
        Terminal.AUTHENTICATION_DENIED,
        Terminal.VALIDATION_DENIED,
    }:
        return False
    stages = {stage.boundary: stage for stage in observation.boundaries}
    for boundary in case.required_boundaries:
        stage = stages.get(boundary)
        if stage is None or stage.truncated or stage.state == "unobserved":
            return False
        if stage.state == "not_reached" and not _not_reached_is_proven(
            stage, observation
        ):
            return False
    if observation.terminal == Terminal.RETRIEVAL_REJECTED and not contained_exposure:
        return False
    return True


def score_case(case: AuditCase, observation: AuditObservation) -> AuditCaseResult:
    forbidden = set(case.forbidden_chunks)
    forbidden_canaries = set(case.forbidden_canaries)
    first_exposure: Boundary | None = None
    for stage in observation.boundaries:
        if stage.state != "observed":
            continue
        identifiers = set(stage.chunk_ids)
        if (
            stage.boundary == Boundary.CITATIONS_CANDIDATE
            and observation.terminal == Terminal.CITATIONS_REJECTED
            and observation.http_status == 502
            and case.expected_status == 502
            and case.citation_challenge is not None
        ):
            identifiers.discard(case.citation_challenge)
        if identifiers & forbidden or any(
            match.canary_id in forbidden_canaries for match in stage.canary_matches
        ):
            first_exposure = stage.boundary
            break
    complete = _coverage_complete(case, observation, first_exposure)
    status: Literal["pass", "fail", "inconclusive"]
    if first_exposure is not None:
        status, reason = "fail", Reason.FORBIDDEN_EVIDENCE
    elif not complete:
        status, reason = "inconclusive", Reason.INCOMPLETE_EVIDENCE
    elif case.positive:
        stages = {stage.boundary: stage for stage in observation.boundaries}
        required = set(case.required_chunks)
        positive_boundaries = (
            Boundary.RETRIEVAL_RAW,
            Boundary.RETRIEVAL_ACCEPTED,
            Boundary.CONTEXT,
            Boundary.CITATIONS_DELIVERED,
        )
        if observation.terminal != Terminal.ANSWERED or any(
            boundary not in stages
            or stages[boundary].state != "observed"
            or not required.issubset(stages[boundary].chunk_ids)
            for boundary in positive_boundaries
        ):
            status, reason = "fail", Reason.POSITIVE_EVIDENCE_MISSING
        else:
            status, reason = "pass", Reason.CONTROL_PASSED
    else:
        status, reason = "pass", Reason.CONTROL_PASSED
    return AuditCaseResult(
        case_id=case.id,
        status=status,
        reason=reason,
        coverage_complete=complete,
        first_exposure=first_exposure,
        observation=observation,
    )
