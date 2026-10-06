import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean
from typing import Annotated, Literal, Self, cast
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from app.audits.artifacts import _checked_path, _read
from app.audits.contracts import (
    AuditModel,
    AuditObservation,
    Boundary,
    Checksum,
    Identifier,
    Terminal,
)
from app.audits.injection_providers import LocalInjectionConfiguration
from app.audits.isolation_reports import IsolationStrategy, _artifact_object
from app.audits.reports import _publish
from app.chat.provider import SYSTEM
from app.evaluation.dataset import UtilityCorpus, generate_utility_corpus
from app.evaluation.metrics import (
    PairedInterval,
    latency_percentiles,
    paired_mean_interval,
    retrieval_scores,
)
from app.evaluation.models import load_embedding_pins

_ERROR = "utility_artifact_invalid"
_MARKERS = (b"AUDITCANARY", b"FACTANSWER", b"Bearer ", b"Authorization")
_NAMESPACE = r"ragelit_audit_[a-z0-9_]{1,32}"
_PROMPT_HASH = hashlib.sha256(SYSTEM.encode()).hexdigest()
Version = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.+-]{1,80}$")]
Duration = Annotated[float, Field(ge=0, strict=True)]
Rate = Annotated[float, Field(ge=0, le=1, strict=True)]
Count = Annotated[int, Field(ge=0, le=1740, strict=True)]
ErrorCode = Literal[
    "audit_login_failed",
    "authentication_failed",
    "membership_inactive",
    "retrieval_unavailable",
    "invalid_retrieval_projection",
    "generation_timeout",
    "generation_unavailable",
    "generation_not_configured",
    "generation_incomplete",
    "generation_response_too_large",
    "invalid_citations",
    "runtime_failed",
]


class GenerationRecord(AuditModel):
    mode: Literal["fixture", "local"]
    local: LocalInjectionConfiguration | None = None
    server_version: Version | None = None
    prompt_hash: Checksum = _PROMPT_HASH
    temperature: Literal[0] = 0
    max_tokens: Literal[1024] = 1024
    response_format: Literal["json_object"] = "json_object"
    timeout_seconds: Literal[30] = 30

    @model_validator(mode="after")
    def validate_generation(self) -> Self:
        if (
            self.prompt_hash != _PROMPT_HASH
            or self.mode == "local"
            and (self.local is None or self.server_version is None)
            or self.mode == "fixture"
            and (self.local is not None or self.server_version is not None)
        ):
            raise ValueError(_ERROR)
        return self


class MachineRecord(AuditModel):
    os: Literal["Windows", "Linux", "Darwin"]
    architecture: Version
    python_version: Version
    logical_cpus: int = Field(strict=True, ge=1, le=4096)
    memory_bytes: int = Field(strict=True, ge=1)


class BenchmarkProvenance(AuditModel):
    generator_id: Literal["natural-utility-v1"] = "natural-utility-v1"
    seed: Literal[20261005] = 20261005
    corpus_hash: Checksum
    embedding_fingerprint: Checksum
    reranker: Literal["none"] = "none"
    generation: GenerationRecord
    git_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    git_dirty: bool = Field(strict=True)
    lock_hashes: tuple[Checksum, ...] = Field(min_length=1, max_length=4)
    machine: MachineRecord
    postgres_version: Version
    qdrant_version: Version

    @model_validator(mode="after")
    def validate_pins(self) -> Self:
        if (
            self.corpus_hash != generate_utility_corpus().checksum
            or self.embedding_fingerprint != load_embedding_pins().fingerprint
        ):
            raise ValueError(_ERROR)
        return self


class UtilityDocumentBinding(AuditModel):
    logical_id: Identifier
    document_id: UUID
    version_id: UUID
    chunk_ids: tuple[UUID, ...] = Field(min_length=1, max_length=20)
    content_hash: Checksum


class CitationRecord(AuditModel):
    chunk_id: UUID
    document_id: UUID
    version_id: UUID


class UtilityQueryRecord(AuditModel):
    query_id: Identifier
    actor_id: Identifier
    relevant_document_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=87)
    permitted_version_ids: tuple[UUID, ...] = Field(min_length=1, max_length=87)
    observation: AuditObservation
    citations: tuple[CitationRecord, ...] = Field(default=(), max_length=20)
    answer_label_match: bool | None = Field(default=None, strict=True)
    error_code: ErrorCode | None = None


class UtilityQueryResult(AuditModel):
    query_id: Identifier
    ranked_document_ids: tuple[Identifier, ...] = Field(max_length=20)
    recall_at_10: Rate | None
    reciprocal_rank_at_10: Rate | None
    retrieval_ms: Duration | None
    delivered_citations: Count
    relevant_citations: Count
    answer_label_match: bool | None
    coverage_complete: bool


class UtilitySummary(AuditModel):
    expected_queries: Literal[87] = 87
    recorded_queries: Count
    retrieval_queries: Count
    recall_at_10: Rate | None
    mrr_at_10: Rate | None
    observed_recall_at_10: Rate | None
    observed_mrr_at_10: Rate | None
    retrieval_p50_ms: Duration | None
    retrieval_p95_ms: Duration | None
    delivered_citations: Count
    relevant_citations: Count
    citation_relevance: Rate | None
    answer_label_queries: Count
    answer_label_rate: Rate | None
    observed_answer_label_rate: Rate | None
    collection_count: int = Field(strict=True, ge=1, le=3)
    retrieval_boundary: Literal["retrieval_accepted"] = "retrieval_accepted"


class UtilityReport(AuditModel):
    schema_version: Literal["utility-1"] = "utility-1"
    run_id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    provenance: BenchmarkProvenance
    strategy: IsolationStrategy
    collection_names: tuple[Annotated[str, Field(max_length=100)], ...] = Field(
        min_length=1, max_length=3
    )
    bindings: tuple[UtilityDocumentBinding, ...] = Field(min_length=87, max_length=87)
    records: tuple[UtilityQueryRecord, ...] = Field(max_length=87)
    results: tuple[UtilityQueryResult, ...] = Field(max_length=87)
    summary: UtilitySummary
    inventory_complete: bool
    coverage_complete: bool
    runtime_failed: bool
    provisional: bool = Field(default=False, strict=True)
    exit_code: Literal[0, 2]
    evidence_notice: Literal[
        "Authored synthetic corpus; citation relevance is not semantic entailment. "
        "Checksums verify integrity, not execution or loaded-server attestation. "
        "Security and cost measurements are separate required benchmark artifacts."
    ] = (
        "Authored synthetic corpus; citation relevance is not semantic entailment. "
        "Checksums verify integrity, not execution or loaded-server attestation. "
        "Security and cost measurements are separate required benchmark artifacts."
    )


def _bindings(
    corpus: UtilityCorpus, bindings: tuple[UtilityDocumentBinding, ...]
) -> dict[UUID, UtilityDocumentBinding]:
    if tuple(item.logical_id for item in bindings) != tuple(
        document.id for document in corpus.documents
    ):
        raise ValueError(_ERROR)
    for attribute in ("document_id", "version_id"):
        if len({getattr(item, attribute) for item in bindings}) != len(bindings):
            raise ValueError(_ERROR)
    by_chunk: dict[UUID, UtilityDocumentBinding] = {}
    for document, item in zip(corpus.documents, bindings, strict=True):
        if item.content_hash != hashlib.sha256(document.text.encode()).hexdigest():
            raise ValueError(_ERROR)
        for chunk_id in item.chunk_ids:
            if chunk_id in by_chunk:
                raise ValueError(_ERROR)
            by_chunk[chunk_id] = item
    return by_chunk


def _collections(strategy: IsolationStrategy, names: tuple[str, ...]) -> None:
    if strategy == "tenant_collections":
        matches = [
            re.fullmatch(rf"({_NAMESPACE})_tenant_([0-9a-f]{{32}})", name)
            for name in names
        ]
        if (
            len(names) != 3
            or len(set(names)) != 3
            or names != tuple(sorted(names))
            or any(match is None for match in matches)
            or len({match.group(1) for match in matches if match is not None}) != 1
        ):
            raise ValueError(_ERROR)
    elif len(names) != 1 or re.fullmatch(_NAMESPACE, names[0]) is None:
        raise ValueError(_ERROR)


def _result(
    record: UtilityQueryRecord,
    corpus: UtilityCorpus,
    bindings: tuple[UtilityDocumentBinding, ...],
    by_chunk: dict[UUID, UtilityDocumentBinding],
) -> UtilityQueryResult:
    query = next((item for item in corpus.queries if item.id == record.query_id), None)
    if query is None:
        raise ValueError(_ERROR)
    allowed = corpus.permitted_document_ids(query.actor_id)
    observation = record.observation
    stages = {stage.boundary: stage for stage in observation.boundaries}
    if (
        record.actor_id != query.actor_id
        or record.relevant_document_ids != query.relevant_document_ids
        or record.permitted_version_ids
        != tuple(item.version_id for item in bindings if item.logical_id in allowed)
        or observation.case_id != query.id
        or set(stages) != set(Boundary)
        or any(
            stage.canary_matches
            or any(chunk_id not in by_chunk for chunk_id in stage.chunk_ids)
            for stage in observation.boundaries
        )
    ):
        raise ValueError(_ERROR)
    terminal = observation.terminal
    if terminal in {Terminal.ANSWERED, Terminal.ABSTAINED}:
        if (
            observation.http_status != 200
            or record.error_code is not None
            or record.answer_label_match is None
            or terminal == Terminal.ABSTAINED
            and record.answer_label_match
            or terminal == Terminal.ANSWERED
            and not record.citations
        ):
            raise ValueError(_ERROR)
    elif record.answer_label_match is not None:
        raise ValueError(_ERROR)
    delivered = stages[Boundary.CITATIONS_DELIVERED]
    if tuple(citation.chunk_id for citation in record.citations) != delivered.chunk_ids:
        raise ValueError(_ERROR)
    for citation in record.citations:
        item = by_chunk[citation.chunk_id]
        if (
            citation.document_id != item.document_id
            or citation.version_id != item.version_id
        ):
            raise ValueError(_ERROR)
    accepted = stages[Boundary.RETRIEVAL_ACCEPTED]
    measured = (
        accepted.state == "observed"
        and not accepted.truncated
        and terminal != Terminal.OBSERVER_FAILED
        and observation.scope_hash is not None
    )
    if measured and any(
        by_chunk[chunk_id].logical_id not in allowed for chunk_id in accepted.chunk_ids
    ):
        raise ValueError(_ERROR)
    ranked = (
        tuple(
            dict.fromkeys(
                by_chunk[chunk_id].logical_id for chunk_id in accepted.chunk_ids
            )
        )
        if measured
        else ()
    )
    relevant = frozenset(query.relevant_document_ids)
    physical = {item.logical_id: item.document_id for item in bindings}
    scores = (
        retrieval_scores(
            tuple(physical[identifier] for identifier in ranked),
            frozenset(physical[identifier] for identifier in relevant),
        )
        if measured
        else None
    )
    context = stages[Boundary.CONTEXT]
    correct = sum(
        by_chunk[citation.chunk_id].logical_id in relevant
        and citation.version_id in record.permitted_version_ids
        and context.state == "observed"
        and not context.truncated
        and citation.chunk_id in context.chunk_ids
        for citation in record.citations
    )
    complete = (
        terminal in {Terminal.ANSWERED, Terminal.ABSTAINED}
        and measured
        and context.state == "observed"
        and (
            terminal != Terminal.ANSWERED
            or all(stage.state == "observed" for stage in stages.values())
        )
        and all(
            stage.state != "unobserved" and not stage.truncated
            for stage in stages.values()
        )
    )
    return UtilityQueryResult(
        query_id=record.query_id,
        ranked_document_ids=ranked,
        recall_at_10=scores.recall_at_10 if scores is not None else None,
        reciprocal_rank_at_10=scores.reciprocal_rank_at_10
        if scores is not None
        else None,
        retrieval_ms=accepted.duration_ms if measured else None,
        delivered_citations=len(record.citations),
        relevant_citations=correct,
        answer_label_match=record.answer_label_match,
        coverage_complete=complete,
    )


def _summary(
    results: tuple[UtilityQueryResult, ...], collections: int
) -> UtilitySummary:
    recalls = [item.recall_at_10 for item in results if item.recall_at_10 is not None]
    ranks = [
        item.reciprocal_rank_at_10
        for item in results
        if item.reciprocal_rank_at_10 is not None
    ]
    latencies = [item.retrieval_ms for item in results if item.retrieval_ms is not None]
    labels = [
        float(item.answer_label_match)
        for item in results
        if item.answer_label_match is not None
    ]
    times = latency_percentiles(latencies) if latencies else None
    citations = sum(item.delivered_citations for item in results)
    relevant = sum(item.relevant_citations for item in results)
    return UtilitySummary(
        recorded_queries=len(results),
        retrieval_queries=len(recalls),
        recall_at_10=fmean(recalls) if len(recalls) == 87 else None,
        mrr_at_10=fmean(ranks) if len(ranks) == 87 else None,
        observed_recall_at_10=fmean(recalls) if recalls else None,
        observed_mrr_at_10=fmean(ranks) if ranks else None,
        retrieval_p50_ms=times.p50_ms if times is not None else None,
        retrieval_p95_ms=times.p95_ms if times is not None else None,
        delivered_citations=citations,
        relevant_citations=relevant,
        citation_relevance=relevant / citations if citations else None,
        answer_label_queries=len(labels),
        answer_label_rate=fmean(labels) if len(labels) == 87 else None,
        observed_answer_label_rate=fmean(labels) if labels else None,
        collection_count=collections,
    )


def build_utility_report(
    provenance: BenchmarkProvenance,
    bindings: tuple[UtilityDocumentBinding, ...],
    records: tuple[UtilityQueryRecord, ...],
    *,
    strategy: str = "shared_pre_filter",
    collection_names: tuple[str, ...],
    runtime_failed: bool = False,
    provisional: bool = False,
) -> UtilityReport:
    provenance = BenchmarkProvenance.model_validate(provenance.model_dump())
    bindings = tuple(
        UtilityDocumentBinding.model_validate(item.model_dump()) for item in bindings
    )
    records = tuple(
        UtilityQueryRecord.model_validate(item.model_dump()) for item in records
    )
    if strategy not in {"shared_pre_filter", "tenant_collections", "lab_post_filter"}:
        raise ValueError(_ERROR)
    checked_strategy = cast(IsolationStrategy, strategy)
    _collections(checked_strategy, collection_names)
    if len({item.query_id for item in records}) != len(records) or len(records) > 87:
        raise ValueError(_ERROR)
    corpus = generate_utility_corpus()
    by_chunk = _bindings(corpus, bindings)
    results = tuple(_result(item, corpus, bindings, by_chunk) for item in records)
    inventory = {item.query_id for item in records} == {
        item.id for item in corpus.queries
    }
    complete = (
        inventory
        and not runtime_failed
        and not provisional
        and all(item.coverage_complete for item in results)
    )
    return UtilityReport(
        provenance=provenance,
        strategy=checked_strategy,
        collection_names=collection_names,
        bindings=bindings,
        records=records,
        results=results,
        summary=_summary(results, len(collection_names)),
        inventory_complete=inventory,
        coverage_complete=complete,
        runtime_failed=runtime_failed,
        provisional=provisional,
        exit_code=0 if complete else 2,
    )


def _checked_report(report: UtilityReport) -> UtilityReport:
    report = UtilityReport.model_validate(report.model_dump())
    replayed = build_utility_report(
        report.provenance,
        report.bindings,
        report.records,
        strategy=report.strategy,
        collection_names=report.collection_names,
        runtime_failed=report.runtime_failed,
        provisional=report.provisional,
    ).model_copy(update={"run_id": report.run_id, "created_at": report.created_at})
    if replayed != report or report.created_at.tzinfo is None:
        raise ValueError(_ERROR)
    return report


class UtilityComparison(AuditModel):
    reference: IsolationStrategy
    candidate: IsolationStrategy
    recall: PairedInterval
    mrr: PairedInterval
    retrieval_ms: PairedInterval


def compare_utility_reports(
    reference: UtilityReport, candidate: UtilityReport
) -> UtilityComparison:
    reference, candidate = _checked_report(reference), _checked_report(candidate)
    if (
        reference.strategy == candidate.strategy
        or not reference.coverage_complete
        or not candidate.coverage_complete
        or reference.provenance != candidate.provenance
        or reference.provenance.git_dirty
        or candidate.provenance.git_dirty
    ):
        raise ValueError(_ERROR)

    def paired(attribute: str) -> PairedInterval:
        sides = []
        for report in (reference, candidate):
            samples: dict[str, float] = {}
            for item in report.results:
                value = getattr(item, attribute)
                if not isinstance(value, float):
                    raise ValueError(_ERROR)
                samples[item.query_id] = value
            sides.append(samples)
        return paired_mean_interval(sides[0], sides[1])

    return UtilityComparison(
        reference=reference.strategy,
        candidate=candidate.strategy,
        recall=paired("recall_at_10"),
        mrr=paired("reciprocal_rank_at_10"),
        retrieval_ms=paired("retrieval_ms"),
    )


def _content(report: UtilityReport) -> bytes:
    content = (_checked_report(report).model_dump_json(indent=2) + "\n").encode()
    if len(content) > 8 * 1024 * 1024 or any(marker in content for marker in _MARKERS):
        raise ValueError(_ERROR)
    return content


def write_utility_report(report: UtilityReport, directory: Path) -> Path:
    content = _content(report)
    directory = directory.absolute()
    if ".." in directory.parts or any(
        part.is_symlink() or part.is_junction()
        for part in (directory, *directory.parents)
    ):
        raise ValueError(_ERROR)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{report.run_id}.json"
    receipt = destination.with_suffix(".sha256.json")
    if any(path.exists() or path.is_symlink() for path in (destination, receipt)):
        raise FileExistsError("utility artifact already exists")
    _publish(
        receipt,
        (
            json.dumps(
                {
                    "filename": destination.name,
                    "sha256": hashlib.sha256(content).hexdigest(),
                },
                sort_keys=True,
            )
            + "\n"
        ).encode(),
    )
    _publish(destination, content)
    return destination


def validate_utility_report(path: Path) -> UtilityReport:
    path = _checked_path(path)
    run_id = UUID(path.stem)
    if path.name != f"{run_id}.json":
        raise ValueError(_ERROR)
    content = _read(path, 8 * 1024 * 1024)
    receipt = _artifact_object(_read(path.with_suffix(".sha256.json"), 4096))
    if receipt != {
        "filename": path.name,
        "sha256": hashlib.sha256(content).hexdigest(),
    }:
        raise ValueError(_ERROR)
    _artifact_object(content)
    report = UtilityReport.model_validate_json(content)
    if report.run_id != run_id:
        raise ValueError(_ERROR)
    _content(report)
    return report
