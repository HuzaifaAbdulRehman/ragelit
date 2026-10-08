import hashlib
import json
from pathlib import Path
from typing import Literal
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
from app.audits.fixtures import generate_fixtures
from app.audits.isolation_reports import (
    IsolationReport,
    validate_isolation_release_directory,
    validate_isolation_report,
    write_isolation_report,
)
from app.audits.reports import build_report
from app.audits.scoring import score_case
from tests.unit.audits.support import metadata

Strategy = Literal["shared_pre_filter", "tenant_collections", "lab_post_filter"]


def report(
    strategy: Strategy = "shared_pre_filter",
    *,
    partial: bool = False,
    runtime: bool = False,
    late: bool = False,
    denied: bool = False,
) -> IsolationReport:
    template = generate_fixtures()
    cases = tuple(
        AuditCase(
            id=logical.id,
            expected_status=logical.expected_status,
            expected_denial_code="membership_inactive"
            if logical.action == "revoke_membership"
            else None,
            positive=logical.positive,
            required_chunks=(UUID(int=1000 + index),) if logical.positive else (),
            forbidden_chunks=(UUID(int=5000 + index),),
            forbidden_canaries=(f"denied-{index}",),
            citation_challenge=UUID(int=5000 + index)
            if logical.action == "challenge_citation"
            else None,
        )
        for index, logical in enumerate(template.cases)
    )
    results = []
    for case in cases:
        terminal = {
            401: Terminal.AUTHENTICATION_DENIED,
            422: Terminal.VALIDATION_DENIED,
            502: Terminal.CITATIONS_REJECTED,
        }.get(
            case.expected_status,
            Terminal.ANSWERED if case.positive and not denied else Terminal.ABSTAINED,
        )
        stages = []
        for sequence, boundary in enumerate(Boundary):
            state: Literal["observed", "not_reached"] = "observed"
            if (
                terminal in {Terminal.AUTHENTICATION_DENIED, Terminal.VALIDATION_DENIED}
                or (
                    terminal == Terminal.ABSTAINED
                    and boundary
                    in {
                        Boundary.OUTPUT_CANDIDATE,
                        Boundary.OUTPUT_DELIVERED,
                        Boundary.CITATIONS_CANDIDATE,
                        Boundary.CITATIONS_DELIVERED,
                    }
                )
                or (
                    terminal == Terminal.CITATIONS_REJECTED
                    and boundary
                    in {
                        Boundary.OUTPUT_DELIVERED,
                        Boundary.CITATIONS_DELIVERED,
                    }
                )
            ):
                state = "not_reached"
            chunks = (
                case.required_chunks
                if not denied
                and boundary
                in {
                    Boundary.RETRIEVAL_RAW,
                    Boundary.RETRIEVAL_ACCEPTED,
                    Boundary.CONTEXT,
                    Boundary.CITATIONS_CANDIDATE,
                    Boundary.CITATIONS_DELIVERED,
                }
                else ()
            )
            if boundary == Boundary.CITATIONS_CANDIDATE and case.citation_challenge:
                chunks = (case.citation_challenge,)
            matches: tuple[CanaryMatch, ...] = ()
            if (
                strategy == "lab_post_filter"
                and case.id == "org-1:user-denied"
                and (
                    boundary == Boundary.RETRIEVAL_RAW
                    or late
                    and boundary == Boundary.CONTEXT
                )
            ):
                chunks = case.forbidden_chunks
                matches = (CanaryMatch(canary_id=case.forbidden_canaries[0]),)
            stages.append(
                BoundaryEvidence(
                    boundary=boundary,
                    sequence=sequence,
                    state=state,
                    chunk_ids=chunks,
                    canary_matches=matches,
                )
            )
        observed = AuditObservation(
            case_id=case.id,
            http_status=case.expected_status,
            terminal=terminal,
            denial_code=case.expected_denial_code,
            scope_hash=None if case.expected_status in {401, 422} else "1" * 64,
            boundaries=tuple(stages),
        )
        results.append(score_case(case, observed))
    provenance = metadata().model_copy(
        update={
            "pack_id": template.pack_id,
            "generator_id": template.generator_id,
            "template_hash": template.checksum,
            "embedding_id": "fixture-topic-v1",
            "provider_id": "fixture-citing-v1",
        }
    )
    audit = build_report(
        cases,
        tuple(results[:1] if partial else results),
        provenance,
        runtime_failed=runtime,
    )
    collections = (
        tuple(f"ragelit_audit_unit_tenant_{UUID(int=index).hex}" for index in (1, 2, 3))
        if strategy == "tenant_collections"
        else ("ragelit_audit_unit",)
    )
    return IsolationReport(
        strategy=strategy, collections=collections, cases=cases, audit=audit
    )


def rewrite(path: Path, content: bytes) -> None:
    path.write_bytes(content)
    path.with_suffix(".sha256.json").write_text(
        json.dumps(
            {
                "filename": path.name,
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "strategy,code,count",
    [
        ("shared_pre_filter", 0, 1),
        ("tenant_collections", 0, 3),
        ("lab_post_filter", 1, 1),
    ],
)
def test_literal_strategy_reports_round_trip_original_bytes(
    tmp_path: Path, strategy: Strategy, code: int, count: int
) -> None:
    artifact = report(strategy)
    path = write_isolation_report(artifact, tmp_path)
    original = path.read_bytes()
    checked = validate_isolation_report(path)
    assert checked == artifact and checked.audit.exit_code == code
    assert len(checked.collections) == count and len(checked.audit.results) == 51
    assert checked.audit.coverage_complete
    assert original == path.read_bytes()
    assert b"AUDITCANARY" not in original and b"Bearer " not in original
    with pytest.raises(FileExistsError):
        write_isolation_report(artifact, tmp_path)
    assert original == path.read_bytes()


@pytest.mark.parametrize(
    "fault",
    [
        "status",
        "gate",
        "positive",
        "expected_status",
        "boundaries",
        "cases",
        "collections",
        "metadata",
    ],
)
def test_recomputed_receipt_cannot_hide_changed_labels_or_scores(
    tmp_path: Path, fault: str
) -> None:
    path = write_isolation_report(report(), tmp_path)
    payload = json.loads(path.read_bytes())
    if fault == "status":
        payload["audit"]["results"][0]["status"] = "fail"
    elif fault == "gate":
        payload["audit"]["exit_code"] = 1
    elif fault == "positive":
        payload["cases"][0]["positive"] = False
        payload["cases"][0]["required_chunks"] = []
    elif fault == "expected_status":
        payload["cases"][0]["expected_status"] = 401
    elif fault == "boundaries":
        payload["cases"][0]["required_boundaries"] = ["context"]
    elif fault == "cases":
        payload["cases"].pop()
    elif fault == "collections":
        payload["collections"] = ["ordinary_collection"]
    else:
        payload["audit"]["metadata"]["embedding_id"] = "unpinned-model"
    rewrite(path, json.dumps(payload).encode())
    with pytest.raises(ValueError):
        validate_isolation_report(path)


@pytest.mark.parametrize(
    "fault",
    [
        "root_duplicate",
        "nested_duplicate",
        "receipt_duplicate",
        "raw_marker",
        "escaped_marker",
        "digest",
        "oversized",
    ],
)
def test_original_byte_reader_rejects_ambiguous_or_unsafe_content(
    tmp_path: Path, fault: str
) -> None:
    path = write_isolation_report(report(), tmp_path)
    content = path.read_bytes()
    if fault == "root_duplicate":
        rewrite(path, b'{"schema_version":"ignored",' + content.lstrip()[1:])
    elif fault == "nested_duplicate":
        rewrite(
            path,
            content.replace(b'"exit_code": 0', b'"exit_code": 1, "exit_code": 0', 1),
        )
    elif fault == "receipt_duplicate":
        receipt = path.with_suffix(".sha256.json")
        receipt.write_text('{"filename":"ignored",' + receipt.read_text()[1:])
    elif fault in {"raw_marker", "escaped_marker"}:
        value = (
            b"AUDITCANARYsecret"
            if fault == "raw_marker"
            else b"\\u0041UDITCANARYsecret"
        )
        rewrite(
            path,
            content.replace(
                b'"denied-0"',
                b'"' + value + b'"',
                1,
            ),
        )
    elif fault == "digest":
        path.write_bytes(content + b" ")
    else:
        rewrite(path, content + b" " * (8 * 1024 * 1024))
    with pytest.raises(ValueError):
        validate_isolation_report(path)


@pytest.mark.parametrize("partial,runtime", [(True, False), (False, True)])
def test_partial_or_runtime_reports_remain_inconclusive(
    tmp_path: Path, partial: bool, runtime: bool
) -> None:
    path = write_isolation_report(report(partial=partial, runtime=runtime), tmp_path)
    checked = validate_isolation_report(path)
    assert checked.audit.exit_code == 2 and not checked.audit.coverage_complete
    assert len(checked.audit.results) == (1 if partial else 51)


def publish(directory: Path) -> None:
    for strategy in ("shared_pre_filter", "tenant_collections", "lab_post_filter"):
        write_isolation_report(report(strategy), directory)


def test_release_validates_exact_three_strategy_gates(tmp_path: Path) -> None:
    publish(tmp_path)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    checked = validate_isolation_release_directory(tmp_path)
    assert {item.strategy: item.audit.exit_code for item in checked} == {
        "shared_pre_filter": 0,
        "tenant_collections": 0,
        "lab_post_filter": 1,
    }
    assert before == {path.name: path.read_bytes() for path in tmp_path.iterdir()}


@pytest.mark.parametrize(
    "fault", ["missing", "extra", "duplicate", "denied", "late", "revision"]
)
def test_release_rejects_missing_results_or_late_leaks(
    tmp_path: Path, fault: str
) -> None:
    write_isolation_report(report(), tmp_path)
    write_isolation_report(report("tenant_collections"), tmp_path)
    if fault == "missing":
        pass
    elif fault == "duplicate":
        write_isolation_report(report(), tmp_path)
    else:
        artifact = report("lab_post_filter", late=fault == "late")
        if fault == "revision":
            artifact = artifact.model_copy(
                update={
                    "audit": artifact.audit.model_copy(
                        update={
                            "metadata": artifact.audit.metadata.model_copy(
                                update={"git_revision": "b" * 40}
                            ),
                        }
                    )
                }
            )
        write_isolation_report(artifact, tmp_path)
        if fault == "extra":
            (tmp_path / "extra.txt").write_text("extra fixture")
        elif fault == "denied":
            shared = next(
                item
                for item in tmp_path.glob("*.json")
                if not item.name.endswith(".sha256.json")
                and json.loads(item.read_bytes())["strategy"] == "shared_pre_filter"
            )
            replacement = report(denied=True).model_copy(
                update={
                    "audit": report(denied=True).audit.model_copy(
                        update={"run_id": UUID(shared.stem)}
                    )
                }
            )
            rewrite(shared, (replacement.model_dump_json() + "\n").encode())
    with pytest.raises(ValueError):
        validate_isolation_release_directory(tmp_path)
