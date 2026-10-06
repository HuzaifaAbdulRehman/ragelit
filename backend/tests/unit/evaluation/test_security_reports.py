import hashlib
import json
from pathlib import Path
from uuid import UUID

import pytest

from app.audits.contracts import AuditObservation, Boundary, CanaryMatch
from app.audits.injection_reports import expected_injection_cases
from tests.unit.audits.test_injection_reports import bindings, report
from tests.unit.evaluation.report_support import provenance


def observations(
    *, leak: bool = False, partial: bool = False
) -> tuple[AuditObservation, ...]:
    raw = tuple(
        result.access_control.observation for result in report(fail=leak).results
    )
    return raw[:2] if partial else raw


@pytest.mark.parametrize(
    "leak,expected_gate,expected_rate", [(False, 0, 0.0), (True, 1, 0.0)]
)
def test_injection_benchmark_replays_all_stage_rates(
    leak: bool,
    expected_gate: int,
    expected_rate: float,
) -> None:
    from app.evaluation.security_reports import build_injection_benchmark

    result = build_injection_benchmark(
        provenance(),
        bindings(),
        observations(leak=leak),
        strategy="shared_pre_filter",
        collection_names=("ragelit_audit_test",),
        provider_profile="obeying" if leak else "resistant",
    )
    rates = {rate.boundary: rate for rate in result.rates}
    assert result.pack_id == "injection-v1"
    assert result.seed == 20261002
    assert rates[Boundary.RETRIEVAL_RAW].expected_cases == 6
    assert rates[Boundary.RETRIEVAL_RAW].measured_cases == 6
    assert rates[Boundary.RETRIEVAL_RAW].primary_rate == 0.0
    assert rates[Boundary.CONTEXT].primary_rate == 0.0
    assert rates[Boundary.OUTPUT_CANDIDATE].primary_rate == expected_rate
    assert rates[Boundary.OUTPUT_DELIVERED].primary_rate == expected_rate
    assert result.injection.primary_rate == (1.0 if leak else 0.0)
    assert result.coverage_complete
    assert result.exit_code == expected_gate


def test_partial_injection_benchmark_keeps_observed_denominators() -> None:
    from app.evaluation.security_reports import build_injection_benchmark

    result = build_injection_benchmark(
        provenance(),
        bindings(),
        observations(leak=True, partial=True),
        strategy="shared_pre_filter",
        collection_names=("ragelit_audit_test",),
        provider_profile="obeying",
    )
    delivered = next(
        rate for rate in result.rates if rate.boundary == Boundary.OUTPUT_DELIVERED
    )
    assert delivered.expected_cases == 6
    assert delivered.recorded_cases == 2
    assert delivered.measured_cases == 2
    assert delivered.primary_rate is None
    assert delivered.observed_rate == 0.0
    assert result.injection.summary.attempted_attacks == 3
    assert result.injection.summary.evaluated_attacks == 1
    assert result.injection.summary.attack_success_rate == 1.0
    assert result.injection.primary_rate is None
    assert not result.coverage_complete
    assert result.exit_code == 2


@pytest.mark.parametrize("field", ["runtime_failed", "provisional"])
def test_runtime_or_provisional_injection_cannot_complete(field: str) -> None:
    from app.evaluation.security_reports import build_injection_benchmark

    result = build_injection_benchmark(
        provenance(),
        bindings(),
        observations(),
        strategy="shared_pre_filter",
        collection_names=("ragelit_audit_test",),
        provider_profile="resistant",
        runtime_failed=field == "runtime_failed",
        provisional=field == "provisional",
    )
    assert not result.coverage_complete
    assert result.exit_code == 2


def test_forbidden_document_disclosure_is_separate_from_injection_success() -> None:
    from app.evaluation.security_reports import build_injection_benchmark

    raw = observations()
    forbidden = expected_injection_cases(bindings(), trials=1)[2].fact_id
    attacked = raw[1].model_copy(
        update={
            "boundaries": tuple(
                stage.model_copy(
                    update={
                        "canary_matches": (
                            *stage.canary_matches,
                            CanaryMatch(canary_id=forbidden),
                        )
                    }
                )
                if stage.boundary == Boundary.OUTPUT_DELIVERED
                else stage
                for stage in raw[1].boundaries
            )
        }
    )
    result = build_injection_benchmark(
        provenance(),
        bindings(),
        (raw[0], attacked, *raw[2:]),
        strategy="shared_pre_filter",
        collection_names=("ragelit_audit_test",),
        provider_profile="resistant",
    )
    delivered = next(
        rate for rate in result.rates if rate.boundary == Boundary.OUTPUT_DELIVERED
    )
    assert delivered.exposed_cases == 1
    assert delivered.measured_cases == 6
    assert delivered.primary_rate == 1 / 6
    assert result.injection.primary_rate == 0.0
    assert result.exit_code == 1


def test_unknown_stage_evidence_and_mismatched_provider_are_rejected() -> None:
    from app.evaluation.security_reports import build_injection_benchmark

    raw = observations()
    stage = raw[0].boundaries[0]
    for change in (
        {"chunk_ids": (UUID(int=999999),)},
        {"canary_matches": (CanaryMatch(canary_id="foreign"),)},
    ):
        altered = raw[0].model_copy(
            update={
                "boundaries": (stage.model_copy(update=change), *raw[0].boundaries[1:])
            }
        )
        with pytest.raises(ValueError):
            build_injection_benchmark(
                provenance(),
                bindings(),
                (altered, *raw[1:]),
                strategy="shared_pre_filter",
                collection_names=("ragelit_audit_test",),
                provider_profile="resistant",
            )
    with pytest.raises(ValueError):
        build_injection_benchmark(
            provenance(),
            bindings(),
            raw,
            strategy="shared_pre_filter",
            collection_names=("ragelit_audit_test",),
            provider_profile="local",
        )


def test_injection_benchmark_artifact_replays_and_never_overwrites(
    tmp_path: Path,
) -> None:
    from app.evaluation.security_reports import (
        build_injection_benchmark,
        validate_injection_benchmark,
        write_injection_benchmark,
    )

    result = build_injection_benchmark(
        provenance(),
        bindings(),
        observations(),
        strategy="shared_pre_filter",
        collection_names=("ragelit_audit_test",),
        provider_profile="resistant",
    )
    path = write_injection_benchmark(result, tmp_path)
    assert validate_injection_benchmark(path) == result
    with pytest.raises(FileExistsError):
        write_injection_benchmark(result, tmp_path)


def test_injection_benchmark_rejects_forged_rates_with_valid_receipt(
    tmp_path: Path,
) -> None:
    from app.evaluation.security_reports import (
        build_injection_benchmark,
        validate_injection_benchmark,
        write_injection_benchmark,
    )

    result = build_injection_benchmark(
        provenance(),
        bindings(),
        observations(leak=True),
        strategy="shared_pre_filter",
        collection_names=("ragelit_audit_test",),
        provider_profile="obeying",
    )
    path = write_injection_benchmark(result, tmp_path)
    data = json.loads(path.read_bytes())
    data["injection"]["primary_rate"] = 0.0
    content = json.dumps(data).encode()
    path.write_bytes(content)
    path.with_suffix(".sha256.json").write_text(
        json.dumps(
            {
                "filename": path.name,
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    )
    with pytest.raises(ValueError):
        validate_injection_benchmark(path)
