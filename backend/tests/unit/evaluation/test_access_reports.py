import hashlib
import json
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import pytest

from app.audits.contracts import (
    AuditObservation,
    Boundary,
    BoundaryEvidence,
    Terminal,
)
from app.evaluation.access_registry import access_registry
from tests.unit.evaluation.report_support import provenance
from tests.unit.evaluation.test_access_registry import RUN_ID, access_inventory


def records(*, lab: bool = False, denied: bool = False) -> tuple[Any, ...]:
    from app.evaluation.access_reports import AccessObservation

    inventory = access_inventory()
    cases = access_registry(inventory, RUN_ID).cases
    result = []
    for index, case in enumerate(cases):
        terminal = {
            401: Terminal.AUTHENTICATION_DENIED,
            422: Terminal.VALIDATION_DENIED,
            502: Terminal.CITATIONS_REJECTED,
        }.get(
            case.expected_status,
            Terminal.ANSWERED if case.positive else Terminal.ABSTAINED,
        )
        if index == 0 and lab:
            terminal = Terminal.RETRIEVAL_REJECTED
        elif index == 0 and denied:
            terminal = Terminal.ABSTAINED
        stages = []
        for sequence, boundary in enumerate(Boundary):
            state: Literal["observed", "not_reached"] = "observed"
            chunks = (
                case.required_chunks
                if boundary
                in {
                    Boundary.RETRIEVAL_RAW,
                    Boundary.RETRIEVAL_ACCEPTED,
                    Boundary.CONTEXT,
                    Boundary.CITATIONS_CANDIDATE,
                    Boundary.CITATIONS_DELIVERED,
                }
                else ()
            )
            if terminal in {Terminal.AUTHENTICATION_DENIED, Terminal.VALIDATION_DENIED}:
                state, chunks = "not_reached", ()
            elif terminal == Terminal.RETRIEVAL_REJECTED:
                state, chunks = (
                    (
                        "observed",
                        (inventory.documents["org-2-organization"].chunk_ids[0],),
                    )
                    if boundary == Boundary.RETRIEVAL_RAW
                    else ("not_reached", ())
                )
            elif terminal == Terminal.ABSTAINED and boundary in {
                Boundary.OUTPUT_CANDIDATE,
                Boundary.OUTPUT_DELIVERED,
                Boundary.CITATIONS_CANDIDATE,
                Boundary.CITATIONS_DELIVERED,
            }:
                state, chunks = "not_reached", ()
            elif terminal == Terminal.CITATIONS_REJECTED:
                if boundary in {
                    Boundary.OUTPUT_DELIVERED,
                    Boundary.CITATIONS_DELIVERED,
                }:
                    state, chunks = "not_reached", ()
                elif boundary == Boundary.CITATIONS_CANDIDATE:
                    assert case.citation_challenge is not None
                    chunks = (case.citation_challenge,)
            stages.append(
                BoundaryEvidence(
                    boundary=boundary, sequence=sequence, state=state, chunk_ids=chunks
                )
            )
        observation = AuditObservation(
            case_id=case.id,
            http_status=503
            if terminal == Terminal.RETRIEVAL_REJECTED
            else case.expected_status,
            terminal=terminal,
            denial_code=case.expected_denial_code,
            scope_hash=None if case.expected_status in {401, 422} else "1" * 64,
            boundaries=tuple(stages),
        )
        result.append(
            AccessObservation(
                observation=observation,
                provider_profile="controlled_citation"
                if case.citation_challenge is not None
                else "fixture",
            )
        )
    return tuple(result)


def benchmark(*, lab: bool = False, denied: bool = False, **kwargs: Any) -> Any:
    from app.evaluation.access_reports import build_access_benchmark

    return build_access_benchmark(
        provenance(),
        access_inventory(),
        records(lab=lab, denied=denied),
        strategy="lab_post_filter" if lab else "shared_pre_filter",
        collection_names=("ragelit_audit_unit",),
        run_id=RUN_ID,
        **kwargs,
    )


@pytest.mark.parametrize("lab,gate,raw_rate", [(False, 0, 0.0), (True, 1, 1 / 51)])
def test_access_report_replays_full_case_cohort_and_contained_exposure(
    lab: bool, gate: int, raw_rate: float
) -> None:
    result = benchmark(lab=lab)
    rates = {rate.boundary: rate for rate in result.rates}
    assert result.pack_id == "access-control-v1"
    assert len(result.results) == 51
    assert (
        sum(
            record.provider_profile == "controlled_citation"
            for record in result.records
        )
        == 3
    )
    assert rates[Boundary.RETRIEVAL_RAW].expected_cases == 51
    assert rates[Boundary.RETRIEVAL_RAW].measured_cases == 51
    assert rates[Boundary.RETRIEVAL_RAW].primary_rate == raw_rate
    assert rates[Boundary.CONTEXT].primary_rate == 0.0
    assert rates[Boundary.OUTPUT_CANDIDATE].primary_rate == 0.0
    assert rates[Boundary.OUTPUT_DELIVERED].primary_rate == 0.0
    assert result.coverage_complete
    assert result.exit_code == gate


def test_access_report_cannot_pass_by_denying_a_positive_control() -> None:
    result = benchmark(denied=True)
    assert result.coverage_complete
    assert result.exit_code == 1
    assert result.results[0].status == "fail"
    assert all(rate.primary_rate == 0.0 for rate in result.rates)


@pytest.mark.parametrize("failure", ["partial", "runtime", "provisional", "empty"])
def test_access_report_preserves_missing_coverage_and_failure_flags(
    failure: str,
) -> None:
    from app.evaluation.access_reports import build_access_benchmark

    raw = records()
    raw = raw[:2] if failure == "partial" else () if failure == "empty" else raw
    result = build_access_benchmark(
        provenance(),
        access_inventory(),
        raw,
        strategy="shared_pre_filter",
        collection_names=("ragelit_audit_unit",),
        run_id=RUN_ID,
        runtime_failed=failure == "runtime",
        provisional=failure == "provisional",
    )
    assert not result.coverage_complete
    assert result.exit_code == 2
    first = result.rates[0]
    assert first.expected_cases == 51
    assert first.recorded_cases == {"partial": 2, "empty": 0}.get(failure, 51)
    assert first.primary_rate == (None if failure in {"partial", "empty"} else 0.0)
    assert first.observed_rate == (None if failure == "empty" else 0.0)


@pytest.mark.parametrize(
    "drift", ["duplicate", "unknown_case", "unknown_chunk", "profile", "namespace"]
)
def test_access_report_rejects_unbound_records_or_wrong_provider_attribution(
    drift: str,
) -> None:
    from app.evaluation.access_reports import build_access_benchmark

    raw = records()
    if drift == "duplicate":
        raw = raw + raw[:1]
    elif drift == "unknown_case":
        raw = (
            raw[0].model_copy(
                update={
                    "observation": raw[0].observation.model_copy(
                        update={"case_id": "unknown"}
                    )
                }
            ),
        ) + raw[1:]
    elif drift == "unknown_chunk":
        stages = raw[0].observation.boundaries
        observation = raw[0].observation.model_copy(
            update={
                "boundaries": (
                    stages[0].model_copy(update={"chunk_ids": (UUID(int=99),)}),
                )
                + stages[1:]
            }
        )
        raw = (raw[0].model_copy(update={"observation": observation}),) + raw[1:]
    elif drift == "profile":
        raw = tuple(
            item.model_copy(update={"provider_profile": "local"})
            if item.observation.case_id == "org-1:citation"
            else item
            for item in raw
        )
    with pytest.raises(ValueError):
        build_access_benchmark(
            provenance(),
            access_inventory(),
            raw,
            strategy="shared_pre_filter",
            collection_names=("ragelit_audit_other",)
            if drift == "namespace"
            else ("ragelit_audit_unit",),
            run_id=RUN_ID,
        )


def test_original_access_artifact_round_trips_without_overwrite(tmp_path: Path) -> None:
    from app.evaluation.access_reports import (
        validate_access_benchmark,
        write_access_benchmark,
    )

    item = benchmark()
    path = write_access_benchmark(item, tmp_path)
    content = path.read_bytes()
    assert validate_access_benchmark(path) == item
    assert path.read_bytes() == content
    assert not any(
        marker in content
        for marker in (b"AUDITCANARY", b"FACTANSWER", b"Bearer ", b"Authorization")
    )
    with pytest.raises(FileExistsError):
        write_access_benchmark(item, tmp_path)


def test_access_artifact_rejects_forged_summary_with_matching_receipt(
    tmp_path: Path,
) -> None:
    from app.evaluation.access_reports import (
        validate_access_benchmark,
        write_access_benchmark,
    )

    path = write_access_benchmark(benchmark(), tmp_path)
    parsed = json.loads(path.read_bytes())
    parsed["rates"][0]["primary_rate"] = 0.5
    content = json.dumps(parsed).encode()
    path.write_bytes(content)
    path.with_suffix(".sha256.json").write_text(
        json.dumps(
            {"filename": path.name, "sha256": hashlib.sha256(content).hexdigest()}
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        validate_access_benchmark(path)
