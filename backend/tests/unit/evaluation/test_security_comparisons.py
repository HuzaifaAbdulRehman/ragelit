import hashlib
import json
from typing import Any, cast
from uuid import UUID, uuid5

import pytest

from app.audits.contracts import Boundary
from app.audits.fixtures import generate_fixtures
from app.audits.target import _instance_text
from app.evaluation.access_reports import AccessBenchmarkReport, build_access_benchmark
from app.evaluation.security_reports import (
    InjectionBenchmarkReport,
    build_injection_benchmark,
)
from tests.unit.audits.test_injection_reports import bindings
from tests.unit.evaluation.report_support import provenance
from tests.unit.evaluation.test_access_registry import RUN_ID
from tests.unit.evaluation.test_access_reports import benchmark
from tests.unit.evaluation.test_security_reports import observations


def injection(*, leak: bool = False, **changes: Any) -> InjectionBenchmarkReport:
    options = {
        "strategy": "lab_post_filter" if leak else "shared_pre_filter",
        "collection_names": ("ragelit_audit_test",),
        "provider_profile": "resistant",
        **changes,
    }
    return build_injection_benchmark(
        provenance(), bindings(), observations(leak=leak), **options
    )


def remap(payload: Any) -> Any:
    chunks: dict[str, str] = {}

    def discover(value: Any) -> None:
        if isinstance(value, dict):
            if "version_id" in value and "chunk_ids" in value:
                version = UUID(int=UUID(value["version_id"]).int ^ 8192)
                chunks.update(
                    {chunk: str(uuid5(version, "0")) for chunk in value["chunk_ids"]}
                )
            for item in value.values():
                discover(item)
        elif isinstance(value, list):
            for item in value:
                discover(item)

    def transform(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: transform(item) for key, item in value.items()}
        if isinstance(value, list):
            return [transform(item) for item in value]
        if isinstance(value, str):
            if value in chunks:
                return chunks[value]
            try:
                identifier = UUID(value)
            except ValueError:
                return value
            return str(
                identifier if identifier == RUN_ID else UUID(int=identifier.int ^ 8192)
            )
        return value

    discover(payload)
    return transform(payload)


def test_access_pairs_logical_cases_not_workspace_identifiers() -> None:
    from app.evaluation.security_comparisons import compare_access_reports

    reference = benchmark()
    candidate = AccessBenchmarkReport.model_validate(
        remap(json.loads(benchmark(lab=True).model_dump_json()))
    )
    result = compare_access_reports(reference, candidate)
    assert result.reference == "shared_pre_filter"
    assert result.candidate == "lab_post_filter"
    assert result.reference_gate == 0
    assert result.candidate_gate == 1
    assert len(result.exposures) == 5
    raw = next(
        item.interval
        for item in result.exposures
        if item.boundary == Boundary.RETRIEVAL_RAW
    )
    assert raw.mean_difference == pytest.approx(1 / 51)
    assert raw.low == 0.0
    assert raw.high == pytest.approx(3 / 51)
    assert raw.paired_queries == 51
    assert raw.resamples == 10000
    assert raw.seed == 20261005
    assert all(item.interval.mean_difference == 0.0 for item in result.exposures[1:])
    assert result.injection_asr is None
    reversed_result = compare_access_reports(candidate, reference)
    reversed_raw = reversed_result.exposures[0].interval
    assert reversed_raw.mean_difference == pytest.approx(-1 / 51)
    assert reversed_raw.low == pytest.approx(-3 / 51)
    assert reversed_raw.high == 0.0


def test_injection_pairs_attacks_only_for_asr_and_preserves_negative_results() -> None:
    from app.evaluation.security_comparisons import compare_injection_reports

    reference = injection()
    candidate = InjectionBenchmarkReport.model_validate(
        remap(json.loads(injection(leak=True).model_dump_json()))
    )
    candidate = candidate.model_copy(
        update={"observations": tuple(reversed(candidate.observations))}
    )
    result = compare_injection_reports(reference, candidate)
    assert result.candidate_gate == 1
    assert result.injection_asr is not None
    assert result.injection_asr.paired_queries == 3
    assert result.injection_asr.mean_difference == 1.0
    assert result.injection_asr.low == result.injection_asr.high == 1.0
    assert all(item.interval.paired_queries == 6 for item in result.exposures)
    assert all(item.interval.mean_difference == 0.0 for item in result.exposures)


@pytest.mark.parametrize("kind", ["access", "injection"])
@pytest.mark.parametrize(
    "failure",
    ["provisional", "runtime_failed", "same_strategy", "source_drift", "forged_rate"],
)
def test_security_pairing_rejects_incomplete_drifted_or_forged_reports(
    kind: str, failure: str
) -> None:
    from app.evaluation.security_comparisons import (
        compare_access_reports,
        compare_injection_reports,
    )

    reference = benchmark() if kind == "access" else injection()
    options = {failure: True} if failure in {"provisional", "runtime_failed"} else {}
    candidate = (
        benchmark(lab=True, **options)
        if kind == "access"
        else injection(leak=True, **options)
    )
    if failure == "same_strategy":
        candidate = candidate.model_copy(update={"strategy": reference.strategy})
    elif failure == "source_drift":
        candidate = candidate.model_copy(
            update={
                "provenance": candidate.provenance.model_copy(
                    update={"git_revision": "b" * 40}
                )
            }
        )
    elif failure == "forged_rate":
        candidate = candidate.model_copy(
            update={
                "rates": (
                    candidate.rates[0].model_copy(update={"primary_rate": 1.0}),
                    *candidate.rates[1:],
                )
            }
        )
    with pytest.raises(ValueError):
        if kind == "access":
            compare_access_reports(
                cast(AccessBenchmarkReport, reference),
                cast(AccessBenchmarkReport, candidate),
            )
        else:
            compare_injection_reports(
                cast(InjectionBenchmarkReport, reference),
                cast(InjectionBenchmarkReport, candidate),
            )


def test_injection_provider_profile_drift_is_not_a_strategy_comparison() -> None:
    from app.evaluation.security_comparisons import compare_injection_reports

    with pytest.raises(ValueError):
        compare_injection_reports(
            injection(), injection(leak=True, provider_profile="obeying")
        )


def test_access_cohort_nonce_changes_real_inputs_and_cannot_be_paired() -> None:
    from app.evaluation.security_comparisons import compare_access_reports

    reference = benchmark()
    other = UUID(int=90001)
    inventory = reference.inventory.model_copy(deep=True)
    documents = {item.id: item for item in generate_fixtures().documents}
    cases = {item.id: item for item in generate_fixtures().cases}
    changed = {}
    for key, instance in inventory.instances.items():
        logical_id = key.partition(":")[2]
        new_key = f"{other}:{logical_id}"
        original = documents[cases[logical_id].document_id]
        assert instance.document is not None
        replaced = instance.previous is not None
        document_hash = hashlib.sha256(
            _instance_text(original, new_key, replacement=replaced).encode()
        ).hexdigest()
        previous = instance.previous
        if previous is not None:
            previous_hash = hashlib.sha256(
                _instance_text(original, new_key).encode()
            ).hexdigest()
            previous = previous.model_copy(update={"content_hash": previous_hash})
        changed[new_key] = instance.model_copy(
            update={
                "document": instance.document.model_copy(
                    update={"content_hash": document_hash}
                ),
                "previous": previous,
            }
        )
    inventory = inventory.model_copy(update={"instances": changed})
    candidate = build_access_benchmark(
        reference.provenance,
        inventory,
        reference.records,
        strategy="lab_post_filter",
        collection_names=(inventory.collection,),
        run_id=other,
    )
    assert candidate.coverage_complete
    with pytest.raises(ValueError):
        compare_access_reports(reference, candidate)
