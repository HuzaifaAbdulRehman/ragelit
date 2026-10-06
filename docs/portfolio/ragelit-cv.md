# RAGelit CV wording

This describes the local implementation, not a completed production release.
Pending branch work may not yet appear on GitHub.

## AI internship or engineering CV

RAGelit: Permission-Aware Document Assistant

Python, FastAPI, PostgreSQL, Qdrant, React, TypeScript

[Repository](https://github.com/HuzaifaAbdulRehman/ragelit)

- Built a multi-tenant document assistant with PostgreSQL row-level security,
  separating organization roles from document-level user and group reading grants.
- Implemented local dense embeddings and BM25 hybrid search with permission-aware
  filtering, background ingestion, cited answers and caller-scoped query traces.
- Developed a 51-case synthetic access-control audit with positive and negative
  controls, stage-level leakage observations and redacted JSON/HTML reports.

## Research-oriented project paragraph

Built an evaluation harness to compare shared-collection pre-filtering,
collection-per-tenant isolation and a guarded retrieve-then-filter lab baseline.
Reports keep retrieval, context exposure and delivered output separate, bind
dataset/model/source provenance, and preserve incomplete coverage and timeouts.
The project studies measurement and access boundaries on synthetic documents;
it is not a published research result or proof of universal model safety.

## Evidence and limits

The [local checkpoint](../verification/2026-10-07-local-release-checkpoint.md)
records the complete 51-case safe audit from a clean Windows clone and inspected
portal screenshots. That audit uses deterministic providers. The full 87-query
real-model baseline measured retrieval, but all generation requests timed out.
Do not claim measured answer accuracy, real-model injection resistance, production
deployment, a passed current-head full release gate or research novelty.

For a short CV, use the three implementation bullets without retrieval percentages.
Benchmark context and negative results belong in the linked evidence, not an
unqualified accuracy claim. No existing CV or personal record was changed.
