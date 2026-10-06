from uuid import UUID

import pytest

from app.audits.contracts import (
    AuditCase,
    AuditObservation,
    Boundary,
    BoundaryEvidence,
    CanaryMatch,
    Terminal,
)
from app.audits.injection_reports import expected_injection_cases
from tests.unit.audits.test_injection_reports import bindings as injection_bindings
from tests.unit.audits.test_injection_reports import report as injection_report


def cases() -> tuple[AuditCase, ...]:
    return (
        AuditCase(
            id="allowed",
            expected_status=200,
            positive=True,
            required_chunks=(UUID(int=1),),
            forbidden_chunks=(UUID(int=9),),
            forbidden_canaries=("forbidden-a",),
        ),
        AuditCase(
            id="denied",
            expected_status=200,
            forbidden_chunks=(UUID(int=8),),
            forbidden_canaries=("forbidden-b",),
        ),
    )


def observation(index: int, *, leak: Boundary | None = None) -> AuditObservation:
    case = cases()[index]
    return AuditObservation(
        case_id=case.id,
        http_status=200,
        terminal=Terminal.ANSWERED,
        scope_hash="a" * 64,
        boundaries=tuple(
            BoundaryEvidence(
                boundary=boundary,
                sequence=sequence,
                chunk_ids=case.forbidden_chunks if boundary == leak else (),
                canary_matches=(CanaryMatch(canary_id=case.forbidden_canaries[0]),)
                if boundary == leak
                else (),
            )
            for sequence, boundary in enumerate(Boundary)
        ),
    )


def test_stage_rates_use_case_denominators_and_keep_candidate_exposure() -> None:
    from app.evaluation.security_metrics import boundary_exposure_rate

    first = observation(0, leak=Boundary.RETRIEVAL_RAW)
    second = observation(1, leak=Boundary.OUTPUT_CANDIDATE)
    raw = boundary_exposure_rate(cases(), (first, second), Boundary.RETRIEVAL_RAW)
    assert raw.expected_cases == 2
    assert raw.recorded_cases == 2
    assert raw.measured_cases == 2
    assert raw.exposed_cases == 1
    assert raw.observed_exposures == 1
    assert raw.primary_rate == 0.5
    assert raw.observed_rate == 0.5
    context = boundary_exposure_rate(cases(), (first, second), Boundary.CONTEXT)
    assert context.primary_rate == 0.0
    candidate = boundary_exposure_rate(
        cases(), (first, second), Boundary.OUTPUT_CANDIDATE
    )
    assert candidate.primary_rate == 0.5
    delivered = boundary_exposure_rate(
        cases(), (first, second), Boundary.OUTPUT_DELIVERED
    )
    assert delivered.primary_rate == 0.0


def test_missing_case_keeps_primary_unknown() -> None:
    from app.evaluation.security_metrics import boundary_exposure_rate

    result = boundary_exposure_rate(cases(), (observation(0),), Boundary.CONTEXT)
    assert result.expected_cases == 2
    assert result.recorded_cases == 1
    assert result.measured_cases == 1
    assert result.primary_rate is None
    assert result.observed_rate == 0.0


def test_truncated_exposure_retains_signal_without_measuring_absence() -> None:
    from app.evaluation.security_metrics import boundary_exposure_rate

    item = observation(0, leak=Boundary.CONTEXT)
    item = item.model_copy(
        update={
            "boundaries": tuple(
                stage.model_copy(update={"truncated": True})
                if stage.boundary == Boundary.CONTEXT
                else stage
                for stage in item.boundaries
            )
        }
    )
    result = boundary_exposure_rate(cases(), (item,), Boundary.CONTEXT)
    assert result.measured_cases == 0
    assert result.exposed_cases == 0
    assert result.observed_exposures == 1
    assert result.primary_rate is None
    assert result.observed_rate is None


@pytest.mark.parametrize(
    "terminal,status,scope,measured",
    [
        (Terminal.RUNTIME_FAILED, 503, "a" * 64, 0),
        (Terminal.CITATIONS_REJECTED, 502, "a" * 64, 1),
        (Terminal.AUTHENTICATION_DENIED, 401, None, 1),
        (Terminal.ABSTAINED, 200, None, 0),
        (Terminal.ABSTAINED, 200, "a" * 64, 1),
        (Terminal.CITATIONS_REJECTED, 200, "a" * 64, 0),
    ],
)
def test_not_reached_requires_boundary_specific_denial_proof(
    terminal: Terminal,
    status: int,
    scope: str | None,
    measured: int,
) -> None:
    from app.evaluation.security_metrics import boundary_exposure_rate

    item = observation(0).model_copy(
        update={
            "terminal": terminal,
            "http_status": status,
            "scope_hash": scope,
            "boundaries": (
                BoundaryEvidence(
                    boundary=Boundary.OUTPUT_DELIVERED, sequence=0, state="not_reached"
                ),
            ),
        }
    )
    result = boundary_exposure_rate(cases(), (item,), Boundary.OUTPUT_DELIVERED)
    assert result.measured_cases == measured
    assert result.primary_rate is None
    assert result.observed_rate == (0.0 if measured else None)


def test_wrong_or_duplicate_case_records_are_rejected() -> None:
    from app.evaluation.security_metrics import boundary_exposure_rate

    first = observation(0)
    for changed in ((first, first), (first.model_copy(update={"case_id": "foreign"}),)):
        with pytest.raises(ValueError, match="security_metric_invalid"):
            boundary_exposure_rate(cases(), changed, Boundary.CONTEXT)
    with pytest.raises(ValueError, match="security_metric_invalid"):
        boundary_exposure_rate((cases()[0], cases()[0]), (first,), Boundary.CONTEXT)
    with pytest.raises(ValueError, match="security_metric_invalid"):
        boundary_exposure_rate((), (), Boundary.CONTEXT)


@pytest.mark.parametrize(
    "boundary,status,expected",
    [
        (Boundary.CITATIONS_CANDIDATE, 502, 0.0),
        (Boundary.CITATIONS_CANDIDATE, 200, 1.0),
        (Boundary.RETRIEVAL_RAW, 502, 1.0),
    ],
)
def test_controlled_rejected_citation_is_not_retrieval_or_output_disclosure(
    boundary: Boundary,
    status: int,
    expected: float,
) -> None:
    from app.evaluation.security_metrics import boundary_exposure_rate

    challenge = UUID(int=99)
    control = AuditCase(
        id="challenge",
        expected_status=502,
        forbidden_chunks=(challenge,),
        citation_challenge=challenge,
    )
    observed = AuditObservation(
        case_id=control.id,
        http_status=status,
        terminal=Terminal.CITATIONS_REJECTED,
        scope_hash="a" * 64,
        boundaries=(
            BoundaryEvidence(
                boundary=boundary,
                sequence=0,
                chunk_ids=(challenge,),
            ),
        ),
    )
    result = boundary_exposure_rate((control,), (observed,), boundary)
    assert result.primary_rate == expected
    assert result.observed_exposures == int(expected)


@pytest.mark.parametrize("failed,expected", [(False, 0.0), (True, 1.0)])
def test_complete_injection_rates_replay_raw_observations(
    failed: bool, expected: float
) -> None:
    from app.evaluation.security_metrics import injection_success_rate

    controls = expected_injection_cases(injection_bindings(), trials=1)
    observed = tuple(
        result.access_control.observation
        for result in injection_report(fail=failed).results
    )
    result = injection_success_rate(controls, observed)
    assert result.summary.attempted_attacks == 3
    assert result.summary.evaluated_attacks == 3
    assert result.summary.evaluated_successes == (3 if failed else 0)
    assert result.primary_rate == expected
    assert result.summary.attack_success_rate == expected


def test_partial_injection_reports_observed_rate_but_not_primary() -> None:
    from app.evaluation.security_metrics import injection_success_rate

    controls = expected_injection_cases(injection_bindings(), trials=1)
    raw = injection_report(fail=True).results
    result = injection_success_rate(
        controls, (raw[0].access_control.observation, raw[1].access_control.observation)
    )
    assert result.summary.attempted_attacks == 3
    assert result.summary.evaluated_attacks == 1
    assert result.summary.evaluated_successes == 1
    assert result.summary.attack_success_rate == 1.0
    assert result.primary_rate is None


def test_failed_benign_control_or_runtime_prevents_primary_injection_rate() -> None:
    from app.evaluation.security_metrics import injection_success_rate

    controls = expected_injection_cases(injection_bindings(), trials=1)
    raw = tuple(
        result.access_control.observation for result in injection_report().results
    )
    missing = raw[0].model_copy(
        update={
            "boundaries": tuple(
                stage.model_copy(update={"canary_matches": ()})
                for stage in raw[0].boundaries
            )
        }
    )
    failed = injection_success_rate(controls, (missing, *raw[1:]))
    assert failed.summary.baseline_failures == 1
    assert not failed.summary.benign_controls_passed
    assert failed.primary_rate is None
    interrupted = injection_success_rate(controls, raw, runtime_failed=True)
    assert interrupted.summary.attack_success_rate == 0.0
    assert interrupted.primary_rate is None
