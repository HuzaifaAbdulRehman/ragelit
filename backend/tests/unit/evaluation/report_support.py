from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING
from uuid import UUID

from app.audits.contracts import AuditObservation, Boundary, BoundaryEvidence, Terminal
from app.evaluation.dataset import generate_utility_corpus
from app.evaluation.models import load_embedding_pins

if TYPE_CHECKING:
    from app.evaluation.reports import (
        BenchmarkProvenance,
        UtilityDocumentBinding,
        UtilityQueryRecord,
        UtilityReport,
    )


@cache
def provenance() -> BenchmarkProvenance:
    from app.evaluation.reports import (
        BenchmarkProvenance,
        GenerationRecord,
        MachineRecord,
    )

    return BenchmarkProvenance(
        corpus_hash=generate_utility_corpus().checksum,
        embedding_fingerprint=load_embedding_pins().fingerprint,
        generation=GenerationRecord(mode="fixture"),
        git_revision="a" * 40,
        git_dirty=False,
        lock_hashes=("b" * 64,),
        machine=MachineRecord(
            os="Windows",
            architecture="AMD64",
            python_version="3.13.5",
            logical_cpus=8,
            memory_bytes=16 * 1024**3,
        ),
        postgres_version="16.4",
        qdrant_version="1.15.4",
    )


@cache
def bindings() -> tuple[UtilityDocumentBinding, ...]:
    import hashlib

    from app.evaluation.reports import UtilityDocumentBinding

    return tuple(
        UtilityDocumentBinding(
            logical_id=document.id,
            document_id=UUID(int=100 + index),
            version_id=UUID(int=1000 + index),
            chunk_ids=(UUID(int=2000 + index),)
            + ((UUID(int=3000),) if index == 0 else ()),
            content_hash=hashlib.sha256(document.text.encode()).hexdigest(),
        )
        for index, document in enumerate(generate_utility_corpus().documents)
    )


@cache
def record(index: int = 0) -> UtilityQueryRecord:
    from app.evaluation.reports import CitationRecord, UtilityQueryRecord

    corpus = generate_utility_corpus()
    query = corpus.queries[index]
    bound = bindings()
    binding = bound[index]
    chunks = (binding.chunk_ids[0],)
    observation = AuditObservation(
        case_id=query.id,
        http_status=200,
        terminal=Terminal.ANSWERED,
        scope_hash="c" * 64,
        boundaries=tuple(
            BoundaryEvidence(
                boundary=boundary,
                sequence=sequence,
                chunk_ids=chunks
                if boundary
                in {
                    Boundary.RETRIEVAL_RAW,
                    Boundary.RETRIEVAL_ACCEPTED,
                    Boundary.CONTEXT,
                    Boundary.CITATIONS_CANDIDATE,
                    Boundary.CITATIONS_DELIVERED,
                }
                else (),
                duration_ms=float(index + 1),
            )
            for sequence, boundary in enumerate(Boundary)
        ),
    )
    return UtilityQueryRecord(
        query_id=query.id,
        actor_id=query.actor_id,
        relevant_document_ids=query.relevant_document_ids,
        permitted_version_ids=tuple(
            item.version_id
            for item in bound
            if item.logical_id in corpus.permitted_document_ids(query.actor_id)
        ),
        observation=observation,
        citations=(
            CitationRecord(
                chunk_id=chunks[0],
                document_id=binding.document_id,
                version_id=binding.version_id,
            ),
        ),
        answer_label_match=True,
    )


def report(
    *, strategy: str = "shared_pre_filter", partial: bool = False
) -> UtilityReport:
    from app.evaluation.reports import build_utility_report

    return build_utility_report(
        provenance(),
        bindings(),
        (record(),) if partial else tuple(record(index) for index in range(87)),
        strategy=strategy,
        collection_names=("ragelit_audit_test",)
        if strategy != "tenant_collections"
        else tuple(
            f"ragelit_audit_test_tenant_{UUID(int=index).hex}" for index in range(1, 4)
        ),
    )
