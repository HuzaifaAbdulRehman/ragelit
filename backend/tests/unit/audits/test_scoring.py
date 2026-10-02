from uuid import UUID

import pytest
from pydantic import ValidationError

from app.audits.contracts import AuditObservation, Boundary, CanaryMatch
from app.audits.reports import build_report
from app.audits.scoring import score_case
from tests.unit.audits.support import FORBIDDEN, case, metadata, observation


def test_complete_positive_control_passes() -> None:
    result = score_case(case(), observation())
    assert result.status == "pass"
    assert result.coverage_complete
    assert result.first_exposure is None
    assert build_report((case(),), (result,), metadata()).exit_code == 0


def test_deny_all_fails_positive_control() -> None:
    evidence = observation()
    evidence = evidence.model_copy(
        update={
            "boundaries": tuple(
                stage.model_copy(update={"chunk_ids": ()})
                for stage in evidence.boundaries
            )
        }
    )
    result = score_case(case(), evidence)
    assert result.status == "fail"
    assert result.reason == "positive_evidence_missing"
    assert result.coverage_complete
    assert build_report((case(),), (result,), metadata()).exit_code == 1


def test_forbidden_retrieval_fails_after_containment() -> None:
    evidence = observation()
    stages = list(evidence.boundaries)
    stages[0] = stages[0].model_copy(update={"chunk_ids": (FORBIDDEN,)})
    evidence = evidence.model_copy(update={"boundaries": tuple(stages)})
    result = score_case(case(positive=False), evidence)
    assert result.status == "fail"
    assert result.reason == "forbidden_evidence"
    assert result.first_exposure == Boundary.RETRIEVAL_RAW


def test_proven_projection_containment_is_a_complete_failed_control() -> None:
    evidence = observation()
    stages = tuple(
        stage.model_copy(update={"chunk_ids": (FORBIDDEN,)})
        if stage.boundary == Boundary.RETRIEVAL_RAW
        else stage.model_copy(update={"state": "not_reached", "chunk_ids": ()})
        for stage in evidence.boundaries
    )
    evidence = evidence.model_copy(
        update={
            "http_status": 503,
            "terminal": "retrieval_rejected",
            "boundaries": stages,
        }
    )
    result = score_case(case(positive=False), evidence)
    assert result.status == "fail"
    assert result.coverage_complete
    assert result.first_exposure == Boundary.RETRIEVAL_RAW
    assert build_report((case(positive=False),), (result,), metadata()).exit_code == 1


@pytest.mark.parametrize("boundary", tuple(Boundary))
def test_forbidden_canary_fails_at_each_observed_boundary(boundary: Boundary) -> None:
    evidence = observation()
    stages = tuple(
        stage.model_copy(
            update={"canary_matches": (CanaryMatch(canary_id="private-fixture"),)}
        )
        if stage.boundary == boundary
        else stage
        for stage in evidence.boundaries
    )
    result = score_case(
        case(positive=False), evidence.model_copy(update={"boundaries": stages})
    )
    assert result.status == "fail"
    assert result.first_exposure == boundary


def test_missing_stage_is_inconclusive() -> None:
    evidence = observation()
    evidence = evidence.model_copy(update={"boundaries": evidence.boundaries[:-1]})
    result = score_case(case(), evidence)
    assert result.status == "inconclusive"
    assert not result.coverage_complete
    assert build_report((case(),), (result,), metadata()).exit_code == 2


def test_unobserved_is_not_empty_evidence() -> None:
    evidence = observation()
    stages = tuple(
        stage.model_copy(update={"state": "unobserved", "chunk_ids": ()})
        if stage.boundary == Boundary.CONTEXT
        else stage
        for stage in evidence.boundaries
    )
    assert (
        score_case(case(), evidence.model_copy(update={"boundaries": stages})).status
        == "inconclusive"
    )


def test_early_authentication_denial_has_proven_not_reached_stages() -> None:
    denial = case(positive=False).model_copy(update={"expected_status": 401})
    evidence = observation().model_copy(
        update={
            "http_status": 401,
            "terminal": "authentication_denied",
            "scope_hash": None,
            "boundaries": tuple(
                stage.model_copy(update={"state": "not_reached", "chunk_ids": ()})
                for stage in observation().boundaries
            ),
        }
    )
    result = score_case(denial, evidence)
    assert result.status == "pass"
    assert result.coverage_complete


def test_not_reached_without_terminal_proof_is_inconclusive() -> None:
    evidence = observation()
    stages = tuple(
        stage.model_copy(update={"state": "not_reached", "chunk_ids": ()})
        for stage in evidence.boundaries
    )
    assert (
        score_case(
            case(positive=False), evidence.model_copy(update={"boundaries": stages})
        ).status
        == "inconclusive"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("terminal", "runtime_failed"),
        ("terminal", "observer_failed"),
        ("http_status", 503),
        ("scope_hash", None),
    ],
)
def test_dependency_or_observer_failure_cannot_pass(field: str, value: object) -> None:
    result = score_case(case(), observation().model_copy(update={field: value}))
    assert result.status == "inconclusive"
    assert not result.coverage_complete


def test_exposure_is_retained_when_later_evidence_is_missing() -> None:
    evidence = observation()
    stage = evidence.boundaries[0].model_copy(update={"chunk_ids": (FORBIDDEN,)})
    result = score_case(
        case(positive=False), evidence.model_copy(update={"boundaries": (stage,)})
    )
    assert result.status == "fail"
    assert not result.coverage_complete
    assert result.first_exposure == Boundary.RETRIEVAL_RAW
    assert build_report((case(positive=False),), (result,), metadata()).exit_code == 2


def test_truncated_evidence_cannot_pass() -> None:
    evidence = observation()
    stages = tuple(
        stage.model_copy(update={"truncated": True}) for stage in evidence.boundaries
    )
    assert (
        score_case(case(), evidence.model_copy(update={"boundaries": stages})).status
        == "inconclusive"
    )


@pytest.mark.parametrize("mode", ["missing", "duplicate", "unknown"])
def test_incomplete_inventory_never_passes(mode: str) -> None:
    result = score_case(case(), observation())
    results = {
        "missing": (),
        "duplicate": (result, result),
        "unknown": (result.model_copy(update={"case_id": "UNKNOWN-001"}),),
    }[mode]
    report = build_report((case(),), results, metadata())
    assert report.exit_code == 2
    assert not report.coverage_complete


def test_citation_challenge_rejection_passes_without_claiming_delivery() -> None:
    challenge = case(positive=False).model_copy(
        update={"expected_status": 502, "citation_challenge": FORBIDDEN}
    )
    evidence = observation()
    stages = tuple(
        stage.model_copy(update={"chunk_ids": (FORBIDDEN,)})
        if stage.boundary == Boundary.CITATIONS_CANDIDATE
        else stage.model_copy(update={"state": "not_reached", "chunk_ids": ()})
        if stage.boundary in {Boundary.OUTPUT_DELIVERED, Boundary.CITATIONS_DELIVERED}
        else stage
        for stage in evidence.boundaries
    )
    evidence = evidence.model_copy(
        update={
            "http_status": 502,
            "terminal": "citations_rejected",
            "boundaries": stages,
        }
    )
    result = score_case(challenge, evidence)
    assert result.status == "pass"
    assert result.first_exposure is None


def test_wrong_candidate_citation_is_not_a_registered_challenge() -> None:
    challenge = case(positive=False).model_copy(
        update={"expected_status": 502, "citation_challenge": UUID(int=99)}
    )
    evidence = observation()
    stages = tuple(
        stage.model_copy(update={"chunk_ids": (FORBIDDEN,)})
        if stage.boundary == Boundary.CITATIONS_CANDIDATE
        else stage
        for stage in evidence.boundaries
    )
    evidence = evidence.model_copy(
        update={
            "http_status": 502,
            "terminal": "citations_rejected",
            "boundaries": stages,
        }
    )
    assert score_case(challenge, evidence).status == "fail"


def test_duplicate_boundary_or_sequence_is_rejected() -> None:
    payload = observation().model_dump()
    stages = list(payload["boundaries"])
    stages[1] = stages[0]
    payload["boundaries"] = stages
    with pytest.raises(ValidationError):
        AuditObservation.model_validate(payload)
