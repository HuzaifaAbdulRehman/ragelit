from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from app.audits.contracts import Boundary, Terminal
from tests.unit.evaluation.report_support import bindings, provenance, record, report


def test_rankings_collapse_chunks_in_first_document_order() -> None:
    from app.evaluation.reports import build_utility_report

    item = record()
    stages = tuple(
        stage.model_copy(
            update={"chunk_ids": (UUID(int=2001), UUID(int=2000), UUID(int=3000))}
        )
        if stage.boundary in {Boundary.RETRIEVAL_RAW, Boundary.RETRIEVAL_ACCEPTED}
        else stage
        for stage in item.observation.boundaries
    )
    item = item.model_copy(
        update={
            "observation": item.observation.model_copy(update={"boundaries": stages})
        }
    )
    result = build_utility_report(
        provenance(), bindings(), (item,), collection_names=("ragelit_audit_test",)
    )
    assert result.results[0].ranked_document_ids == (
        "org-1-support-response",
        "org-1-change-notice",
    )
    assert result.results[0].recall_at_10 == 1.0
    assert result.results[0].reciprocal_rank_at_10 == 0.5
    assert result.summary.recall_at_10 is None
    assert result.summary.observed_recall_at_10 == 1.0


def test_full_cohort_uses_literal_denominators_and_linear_latencies() -> None:
    result = report()
    assert result.inventory_complete
    assert result.coverage_complete
    assert result.exit_code == 0
    assert result.summary.expected_queries == 87
    assert result.summary.recorded_queries == 87
    assert result.summary.retrieval_queries == 87
    assert result.summary.recall_at_10 == 1.0
    assert result.summary.mrr_at_10 == 1.0
    assert result.summary.retrieval_p50_ms == 44.0
    assert result.summary.retrieval_p95_ms == pytest.approx(82.7)
    assert result.summary.delivered_citations == 87
    assert result.summary.relevant_citations == 87
    assert result.summary.citation_relevance == 1.0
    assert result.summary.answer_label_rate == 1.0
    assert result.summary.collection_count == 1
    assert result.provenance.generation.mode == "fixture"


def test_measured_miss_is_zero_but_missing_retrieval_is_unknown() -> None:
    from app.evaluation.reports import build_utility_report

    item = record()
    missed = tuple(
        stage.model_copy(
            update={
                "chunk_ids": (),
                "state": "not_reached"
                if stage.boundary
                not in {
                    Boundary.RETRIEVAL_RAW,
                    Boundary.RETRIEVAL_ACCEPTED,
                    Boundary.CONTEXT,
                }
                else "observed",
            }
        )
        for stage in item.observation.boundaries
    )
    item = item.model_copy(
        update={
            "observation": item.observation.model_copy(
                update={"terminal": Terminal.ABSTAINED, "boundaries": missed}
            ),
            "citations": (),
            "answer_label_match": False,
        }
    )
    result = build_utility_report(
        provenance(), bindings(), (item,), collection_names=("ragelit_audit_test",)
    )
    assert result.results[0].recall_at_10 == 0.0
    assert result.summary.retrieval_queries == 1
    assert result.summary.answer_label_queries == 1
    assert result.summary.citation_relevance is None
    assert result.exit_code == 2
    failed = tuple(
        stage.model_copy(
            update={
                "state": "unobserved",
                "chunk_ids": (),
                "duration_ms": 0.0,
            }
        )
        for stage in item.observation.boundaries
    )
    item = item.model_copy(
        update={
            "observation": item.observation.model_copy(
                update={
                    "terminal": Terminal.RUNTIME_FAILED,
                    "http_status": 503,
                    "boundaries": failed,
                    "scope_hash": None,
                }
            ),
            "error_code": "retrieval_unavailable",
            "answer_label_match": None,
        }
    )
    result = build_utility_report(
        provenance(), bindings(), (item,), collection_names=("ragelit_audit_test",)
    )
    assert result.results[0].recall_at_10 is None
    assert result.results[0].retrieval_ms is None
    assert result.summary.retrieval_queries == 0
    assert result.summary.observed_recall_at_10 is None
    assert result.summary.retrieval_p50_ms is None


def test_citation_relevance_is_not_claimed_as_semantic_entailment() -> None:
    from app.evaluation.reports import CitationRecord, build_utility_report

    item = record()
    cited = (UUID(int=2000), UUID(int=2001))
    stages = tuple(
        stage.model_copy(update={"chunk_ids": cited})
        if stage.boundary
        in {
            Boundary.RETRIEVAL_RAW,
            Boundary.RETRIEVAL_ACCEPTED,
            Boundary.CONTEXT,
            Boundary.CITATIONS_CANDIDATE,
            Boundary.CITATIONS_DELIVERED,
        }
        else stage
        for stage in item.observation.boundaries
    )
    item = item.model_copy(
        update={
            "observation": item.observation.model_copy(update={"boundaries": stages}),
            "citations": (
                item.citations[0],
                CitationRecord(
                    chunk_id=cited[1],
                    document_id=UUID(int=101),
                    version_id=UUID(int=1001),
                ),
            ),
            "answer_label_match": False,
        }
    )
    result = build_utility_report(
        provenance(), bindings(), (item,), collection_names=("ragelit_audit_test",)
    )
    assert result.results[0].delivered_citations == 2
    assert result.results[0].relevant_citations == 1
    assert result.summary.citation_relevance == 0.5
    assert result.summary.observed_answer_label_rate == 0.0


@pytest.mark.parametrize(
    "change",
    [
        {"actor_id": "org-2-engineering-member"},
        {"relevant_document_ids": ("org-1-support-response",)},
        {"permitted_version_ids": (UUID(int=1000),)},
        {"answer_label_match": None},
        {"error_code": "Bearer secret"},
    ],
)
def test_record_rejects_changed_scope_labels_and_unsafe_errors(
    change: dict[str, Any],
) -> None:
    from app.evaluation.reports import build_utility_report

    with pytest.raises(ValueError):
        build_utility_report(
            provenance(),
            bindings(),
            (record().model_copy(update=change),),
            collection_names=("ragelit_audit_test",),
        )


@pytest.mark.parametrize(
    "change",
    [
        {"git_revision": "wrong"},
        {"corpus_hash": "0" * 64},
        {"embedding_fingerprint": "0" * 64},
        {"seed": 20261006},
        {"reranker": "other"},
    ],
)
def test_provenance_is_bound_to_fixed_corpus_and_model_settings(
    change: dict[str, Any],
) -> None:
    from app.evaluation.reports import build_utility_report

    with pytest.raises(ValueError):
        build_utility_report(
            provenance().model_copy(update=change),
            bindings(),
            (record(),),
            collection_names=("ragelit_audit_test",),
        )


def test_duplicate_queries_and_chunk_aliases_are_rejected() -> None:
    from app.evaluation.reports import build_utility_report

    with pytest.raises(ValueError):
        build_utility_report(
            provenance(),
            bindings(),
            (record(), record()),
            collection_names=("ragelit_audit_test",),
        )
    bound = bindings()
    alias = bound[1].model_copy(update={"chunk_ids": bound[0].chunk_ids})
    with pytest.raises(ValueError):
        build_utility_report(
            provenance(),
            (bound[0], alias, *bound[2:]),
            (record(),),
            collection_names=("ragelit_audit_test",),
        )


def test_partial_inventory_and_runtime_failure_remain_incomplete() -> None:
    from app.evaluation.reports import build_utility_report

    partial = report(partial=True)
    assert not partial.inventory_complete
    assert not partial.coverage_complete
    assert partial.summary.recorded_queries == 1
    assert partial.exit_code == 2
    full = report()
    failed = build_utility_report(
        full.provenance,
        full.bindings,
        full.records,
        collection_names=full.collection_names,
        runtime_failed=True,
    )
    assert failed.inventory_complete
    assert not failed.coverage_complete
    assert failed.exit_code == 2


def test_paired_comparison_uses_exact_logical_ids_and_fixed_provenance() -> None:
    from app.evaluation.reports import compare_utility_reports

    intervals = compare_utility_reports(report(), report(strategy="tenant_collections"))
    assert intervals.recall.mean_difference == 0.0
    assert intervals.mrr.low == intervals.mrr.high == 0.0
    assert intervals.retrieval_ms.paired_queries == 87
    assert intervals.recall.seed == 20261005
    assert intervals.recall.resamples == 10000


@pytest.mark.parametrize("changed", ["partial", "source", "provider", "strategy"])
def test_pairing_refuses_partial_mixed_source_and_mixed_provider(changed: str) -> None:
    from app.evaluation.reports import GenerationRecord, compare_utility_reports

    first = report()
    second = report(strategy="tenant_collections", partial=changed == "partial")
    if changed == "source":
        second = second.model_copy(
            update={
                "provenance": second.provenance.model_copy(update={"git_dirty": True})
            }
        )
    elif changed == "provider":
        from app.audits.injection_providers import LocalInjectionConfiguration

        generation = GenerationRecord(
            mode="local",
            server_version="llama.cpp-b1234",
            local=LocalInjectionConfiguration(
                base_url="http://127.0.0.1:8080/v1",
                model="test-model",
                weights_hash="d" * 64,
            ),
        )
        second = second.model_copy(
            update={
                "provenance": second.provenance.model_copy(
                    update={"generation": generation}
                )
            }
        )
    elif changed == "strategy":
        second = first
    with pytest.raises(ValueError):
        compare_utility_reports(first, second)


def test_write_and_replay_keep_partial_results_and_do_not_overwrite(
    tmp_path: Path,
) -> None:
    from app.evaluation.reports import validate_utility_report, write_utility_report

    original = report(partial=True)
    path = write_utility_report(original, tmp_path)
    assert validate_utility_report(path) == original
    content = path.read_bytes()
    assert all(
        marker not in content
        for marker in (
            b"Bearer ",
            b"AUDITCANARY",
            b"FACTANSWER",
            b"expected_answer",
            b'"answer":',
            b'"question":',
            b'"text":',
            b"password",
        )
    )
    assert len(content) < 256 * 1024
    with pytest.raises(FileExistsError):
        write_utility_report(original, tmp_path)
    assert path.read_bytes() == content


@pytest.mark.parametrize(
    "change", ["summary", "omission", "duplicate", "receipt", "escaped_marker"]
)
def test_offline_replay_rejects_rewritten_metrics_inventory_and_secrets(
    tmp_path: Path,
    change: str,
) -> None:
    from app.evaluation.reports import validate_utility_report, write_utility_report

    path = write_utility_report(report(), tmp_path)
    payload = json.loads(path.read_bytes())
    if change == "summary":
        payload["summary"]["recall_at_10"] = 0.0
    elif change == "omission":
        payload["records"] = payload["records"][1:]
    elif change == "duplicate":
        payload["records"][1] = payload["records"][0]
    elif change == "escaped_marker":
        payload["provenance"]["machine"]["architecture"] = "AUDITCANARYhidden"
    content = json.dumps(payload).encode().replace(b"AUDITCANARY", b"\\u0041UDITCANARY")
    path.write_bytes(content)
    if change != "receipt":
        path.with_suffix(".sha256.json").write_text(
            json.dumps(
                {
                    "filename": path.name,
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            )
        )
    with pytest.raises(ValueError):
        validate_utility_report(path)


def test_duplicate_json_keys_are_rejected_with_valid_receipt(tmp_path: Path) -> None:
    from app.evaluation.reports import validate_utility_report, write_utility_report

    path = write_utility_report(report(partial=True), tmp_path)
    content = path.read_bytes().replace(
        b'"schema_version": "utility-1"',
        b'"schema_version": "utility-1", "schema_version": "utility-1"',
    )
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
        validate_utility_report(path)


def test_abstention_after_generation_keeps_actual_delivered_citations() -> None:
    from app.evaluation.reports import build_utility_report

    item = record()
    item = item.model_copy(
        update={
            "observation": item.observation.model_copy(
                update={"terminal": Terminal.ABSTAINED}
            ),
            "answer_label_match": False,
        }
    )
    result = build_utility_report(
        provenance(),
        bindings(),
        (item,),
        collection_names=("ragelit_audit_test",),
    )
    assert result.results[0].coverage_complete
    assert result.results[0].answer_label_match is False
    assert result.results[0].delivered_citations == 1


def test_observer_failure_preserves_known_delivered_ids_without_scoring_retrieval() -> (
    None
):
    from app.evaluation.reports import build_utility_report

    item = record()
    item = item.model_copy(
        update={
            "observation": item.observation.model_copy(
                update={"terminal": Terminal.OBSERVER_FAILED}
            ),
            "answer_label_match": None,
        }
    )
    result = build_utility_report(
        provenance(),
        bindings(),
        (item,),
        collection_names=("ragelit_audit_test",),
    )
    assert result.results[0].delivered_citations == 1
    assert result.results[0].recall_at_10 is None
    assert not result.results[0].coverage_complete


@pytest.mark.parametrize("kind", ["truncated", "missing_scope", "unobserved"])
def test_untrustworthy_retrieval_does_not_become_a_zero_latency_sample(
    kind: str,
) -> None:
    from app.evaluation.reports import build_utility_report

    item = record()
    stages = tuple(
        stage.model_copy(
            update={
                "truncated": kind == "truncated",
                "state": "unobserved" if kind == "unobserved" else stage.state,
                "chunk_ids": () if kind == "unobserved" else stage.chunk_ids,
            }
        )
        if stage.boundary == Boundary.RETRIEVAL_ACCEPTED
        else stage
        for stage in item.observation.boundaries
    )
    item = item.model_copy(
        update={
            "observation": item.observation.model_copy(
                update={
                    "boundaries": stages,
                    "scope_hash": None
                    if kind == "missing_scope"
                    else item.observation.scope_hash,
                }
            )
        }
    )
    result = build_utility_report(
        provenance(),
        bindings(),
        (item,),
        collection_names=("ragelit_audit_test",),
    )
    assert result.results[0].retrieval_ms is None
    assert result.results[0].recall_at_10 is None
    assert result.summary.retrieval_queries == 0
    assert not result.results[0].coverage_complete


def test_generation_failure_keeps_completed_retrieval_measurement() -> None:
    from app.evaluation.reports import build_utility_report

    item = record()
    stages = tuple(
        stage.model_copy(update={"state": "unobserved", "chunk_ids": ()})
        if stage.boundary
        not in {
            Boundary.RETRIEVAL_RAW,
            Boundary.RETRIEVAL_ACCEPTED,
            Boundary.CONTEXT,
        }
        else stage
        for stage in item.observation.boundaries
    )
    item = item.model_copy(
        update={
            "observation": item.observation.model_copy(
                update={
                    "terminal": Terminal.RUNTIME_FAILED,
                    "http_status": 504,
                    "boundaries": stages,
                }
            ),
            "answer_label_match": None,
            "citations": (),
            "error_code": "generation_timeout",
        }
    )
    result = build_utility_report(
        provenance(),
        bindings(),
        (item,),
        collection_names=("ragelit_audit_test",),
    )
    assert result.results[0].recall_at_10 == 1.0
    assert result.results[0].retrieval_ms == 1.0
    assert not result.results[0].coverage_complete
    assert result.summary.answer_label_queries == 0


def test_answered_without_citations_cannot_claim_complete_observation() -> None:
    from app.evaluation.reports import build_utility_report

    item = record()
    stages = tuple(
        stage.model_copy(update={"chunk_ids": ()})
        if stage.boundary
        in {Boundary.CITATIONS_CANDIDATE, Boundary.CITATIONS_DELIVERED}
        else stage
        for stage in item.observation.boundaries
    )
    item = item.model_copy(
        update={
            "observation": item.observation.model_copy(update={"boundaries": stages}),
            "citations": (),
        }
    )
    with pytest.raises(ValueError):
        build_utility_report(
            provenance(),
            bindings(),
            (item,),
            collection_names=("ragelit_audit_test",),
        )


@pytest.mark.parametrize("kind", ["ids", "canary", "document", "unknown_query"])
def test_replay_rejects_unmapped_or_mislabeled_evidence(kind: str) -> None:
    from app.audits.contracts import CanaryMatch
    from app.evaluation.reports import build_utility_report

    item = record()
    if kind == "document":
        item = item.model_copy(
            update={
                "citations": (
                    item.citations[0].model_copy(update={"version_id": UUID(int=1001)}),
                ),
            }
        )
    elif kind == "unknown_query":
        item = item.model_copy(update={"query_id": "unknown:query"})
    else:
        stages = tuple(
            stage.model_copy(
                update={
                    "chunk_ids": (UUID(int=9999),)
                    if kind == "ids"
                    else stage.chunk_ids,
                    "canary_matches": (CanaryMatch(canary_id="invented-canary"),)
                    if kind == "canary"
                    else (),
                }
            )
            if stage.boundary == Boundary.RETRIEVAL_RAW
            else stage
            for stage in item.observation.boundaries
        )
        item = item.model_copy(
            update={
                "observation": item.observation.model_copy(
                    update={"boundaries": stages}
                )
            }
        )
    with pytest.raises(ValueError):
        build_utility_report(
            provenance(),
            bindings(),
            (item,),
            collection_names=("ragelit_audit_test",),
        )


@pytest.mark.parametrize("kind", ["namespace", "count", "duplicate", "strategy"])
def test_collection_counts_come_from_validated_strategy_inventory(kind: str) -> None:
    from app.evaluation.reports import build_utility_report

    names: tuple[str, ...] = ("ragelit_audit_test",)
    strategy = "shared_pre_filter"
    if kind == "namespace":
        names = ("other_application",)
    elif kind == "count":
        strategy = "tenant_collections"
    elif kind == "duplicate":
        names = ("ragelit_audit_test", "ragelit_audit_test")
    else:
        strategy = "unknown"
    with pytest.raises(ValueError):
        build_utility_report(
            provenance(),
            bindings(),
            (record(),),
            strategy=strategy,
            collection_names=names,
        )


def test_missing_receipt_and_oversized_content_fail_closed(tmp_path: Path) -> None:
    from app.evaluation.reports import validate_utility_report, write_utility_report

    path = write_utility_report(report(partial=True), tmp_path)
    receipt = path.with_suffix(".sha256.json")
    original = receipt.read_bytes()
    receipt.unlink()
    with pytest.raises(FileNotFoundError):
        validate_utility_report(path)
    receipt.write_bytes(original)
    path.write_bytes(b" " * (8 * 1024 * 1024 + 1))
    with pytest.raises(ValueError):
        validate_utility_report(path)


def test_linked_directory_is_not_an_artifact_destination(tmp_path: Path) -> None:
    from app.evaluation.reports import write_utility_report

    link = tmp_path / "link"
    directory = tmp_path / "real"
    directory.mkdir()
    try:
        link.symlink_to(directory, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symbolic links is unavailable on this host")
    with pytest.raises(ValueError):
        write_utility_report(report(partial=True), link)
    assert not tuple(directory.iterdir())


def test_abstention_cannot_hide_a_missing_context_stage() -> None:
    from app.evaluation.reports import build_utility_report

    item = record()
    stages = tuple(
        stage.model_copy(
            update={
                "chunk_ids": (),
                "state": "observed"
                if stage.boundary
                in {
                    Boundary.RETRIEVAL_RAW,
                    Boundary.RETRIEVAL_ACCEPTED,
                }
                else "not_reached",
            }
        )
        for stage in item.observation.boundaries
    )
    item = item.model_copy(
        update={
            "observation": item.observation.model_copy(
                update={
                    "terminal": Terminal.ABSTAINED,
                    "boundaries": stages,
                }
            ),
            "citations": (),
            "answer_label_match": False,
        }
    )
    result = build_utility_report(
        provenance(),
        bindings(),
        (item,),
        collection_names=("ragelit_audit_test",),
    )
    assert not result.results[0].coverage_complete


def test_full_summary_keeps_a_measured_miss_and_uses_accepted_latency() -> None:
    from app.evaluation.reports import build_utility_report

    first = record()
    stages = tuple(
        stage.model_copy(
            update={
                "chunk_ids": (),
                "state": "observed"
                if stage.boundary
                in {
                    Boundary.RETRIEVAL_RAW,
                    Boundary.RETRIEVAL_ACCEPTED,
                    Boundary.CONTEXT,
                }
                else "not_reached",
                "duration_ms": 100.0
                if stage.boundary == Boundary.RETRIEVAL_ACCEPTED
                else 1.0,
            }
        )
        for stage in first.observation.boundaries
    )
    first = first.model_copy(
        update={
            "observation": first.observation.model_copy(
                update={
                    "terminal": Terminal.ABSTAINED,
                    "boundaries": stages,
                }
            ),
            "citations": (),
            "answer_label_match": False,
        }
    )
    result = build_utility_report(
        provenance(),
        bindings(),
        (first, *(record(index) for index in range(1, 87))),
        collection_names=("ragelit_audit_test",),
    )
    assert result.coverage_complete
    assert result.summary.recall_at_10 == pytest.approx(86 / 87)
    assert result.summary.mrr_at_10 == pytest.approx(86 / 87)
    assert result.summary.answer_label_rate == pytest.approx(86 / 87)
    assert result.summary.retrieval_p50_ms == 45.0
    assert result.summary.retrieval_p95_ms == pytest.approx(83.7)
    assert result.summary.retrieval_queries == 87
    assert result.summary.delivered_citations == 86


def test_provisional_full_snapshot_cannot_claim_a_completed_run(tmp_path: Path) -> None:
    from app.evaluation.reports import (
        build_utility_report,
        validate_utility_report,
        write_utility_report,
    )

    full = report()
    snapshot = build_utility_report(
        full.provenance,
        full.bindings,
        full.records,
        collection_names=full.collection_names,
        provisional=True,
    )
    assert snapshot.inventory_complete
    assert not snapshot.coverage_complete
    assert snapshot.exit_code == 2
    assert not snapshot.runtime_failed
    path = write_utility_report(snapshot, tmp_path)
    assert validate_utility_report(path).provisional
