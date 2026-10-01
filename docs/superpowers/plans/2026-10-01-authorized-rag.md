# Authorized RAG Implementation Plan

> Use `superpowers:executing-plans` to implement these tasks in this worktree.

Goal: upload supported documents, process them durably, and answer questions
using only current permitted evidence, with citations and a safe trace.

Spec: `docs/superpowers/specs/2026-09-29-ragelit-security-design.md`

Architecture: PostgreSQL owns document lifecycle and grants. A scoped worker
extracts and chunks uploads, then publishes dense and BM25 vectors to Qdrant.
Retrieval checks current relational eligibility and applies tenant, active,
version, and grant filters to both Qdrant branches. Chat accepts only the
retriever's immutable chunks and validates model citations.

Stack: existing FastAPI, SQLAlchemy, PostgreSQL, React contracts; Qdrant client,
FastEmbed, and pypdf for capabilities absent from the existing dependencies.

## Constraints

- Keep the M1 session, role, RLS, and generic cross-tenant errors.
- Default new documents to restricted access with no implicit uploader grant.
- Accept TXT, Markdown, DOCX, and text PDFs; reject empty or image-only input.
- Stream at most 25 MiB; use server-generated storage paths and SHA-256.
- Worker connections use the application role and an explicit organization.
- Qdrant failures deny retrieval and preserve a safe error code.
- Current database eligibility is part of the pre-filter, not post-filtering.
- Provider credentials come only from explicit application configuration.
- Keep synthetic providers in tests. No paid model is required by CI.
- Traces store identifiers, decisions, and timings, never source bodies.
- Commit each verified task locally. Push only when requested.

## Review focus

- Re-upload after failure must not duplicate an active version (Tasks 2, 3).
- Expired job leases must not let an old worker publish over a replacement
  claim (Task 3).
- Grant changes with a stale index must fail closed (Tasks 4, 5).
- Malformed archives and traversal filenames must not escape storage or
  exhaust extraction limits (Task 2).
- Model timeouts and invented citations must preserve retrieval evidence
  without returning ungrounded answers (Task 6).

### Task 1: Document metadata and tenant policies

Files: `backend/app/documents/models.py`, `schemas.py`,
`backend/app/alembic/versions/0003_documents.py`, Alembic model imports,
`backend/tests/api/test_documents_schema.py`, migration regression tests.

Produces Document, DocumentVersion, DocumentGrant, IngestionJob; composite
tenant foreign keys, tenant checksum uniqueness, forced RLS, and runtime grants.

- [ ] Write tests for cross-tenant writes, absent-context reads, constraints,
  and migration metadata equivalence; observe RED.
- [ ] Add typed models and migration; preserve the existing naming convention.
- [ ] Run `uv run pytest tests/api/test_documents_schema.py
  tests/test_migration_head.py -q`; expect PASS.
- [ ] Commit `add document lifecycle schema`.

### Task 2: Safe uploads and text extraction

Files: `backend/app/documents/storage.py`, `extraction.py`, `chunking.py`,
`service.py`, `api.py`; config, router, generated contracts; unit/API tests.

Produces stream_upload() -> StoredUpload, extract_text() -> tuple[TextSection],
chunk_sections() -> tuple[TextChunk], and document upload/list/detail APIs.
Chunks carry deterministic IDs and page/paragraph/line provenance.

- [ ] Write size, malformed-content, filename, deduplication, role, and tenant
  tests; observe RED.
- [ ] Store uploads beneath organization/version UUIDs; create one queued
  document/version/job transaction; remove unused temporary files.
- [ ] Bound extracted text and DOCX archive expansion. Reject empty evidence.
- [ ] Run new unit and document API tests; expect PASS.
- [ ] Commit `add safe document upload and extraction`.

### Task 3: Durable worker and vector publication

Files: `backend/app/workers/ingestion.py`, `backend/app/retrieval/embeddings.py`,
`store.py`, config/dependency lock, worker integration tests.

Produces claim_job(organization_id, session) -> JobClaim | None, run_once(),
EmbeddingProvider, FastEmbedProvider, and QdrantChunkStore. Use named dense and
BM25 sparse vectors, IDF modifier, and indexed tenant/grant/version payloads.

- [ ] Write lease recovery, duplicate claims, partial publication, retry, and
  real Qdrant activation tests; observe RED.
- [ ] Claim via `FOR UPDATE SKIP LOCKED`; persist a bounded lease and claim ID.
- [ ] Extract, chunk, and embed in bounded batches. Publish inactive points;
  verify count before activation. Revalidate claim before completing.
- [ ] Failure records only a stable code. Retry keeps the same version.
- [ ] Run worker and vector tests; expect PASS.
- [ ] Commit `index documents with a durable worker`.

### Task 4: Mandatory authorized hybrid retrieval

Files: `backend/app/retrieval/contracts.py`, `authorization.py`, `service.py`,
unit filter tests and real PostgreSQL/Qdrant positive and negative tests.

Produces AuthorizedChunk and AuthorizedRetriever.search(scope, query, limit).
Derive eligible active version IDs from current document grants. Apply that
allowlist plus organization/active/grant conditions to both prefetch branches
and outer RRF query. No eligible version means no vector call.

- [ ] Write tenant/group/direct-user positives and negatives; prove stale
  grants and deleted versions cannot return candidates; observe RED.
- [ ] Implement bounded query/limit and fail-closed provider/store handling.
- [ ] Run hybrid retrieval integration suite; expect PASS.
- [ ] Commit `enforce grants inside hybrid retrieval`.

### Task 5: Permission changes, deletion, and retries

Files: document lifecycle APIs/services, vector payload updates, API regressions.

Produces grant editor, soft deletion, versions listing, and explicit retry.
Validate grant targets against active same-tenant memberships/groups. Serialize
document mutations. Update vector visibility before reporting success; a failed
projection update returns a retriable error, never a permissive fallback.

- [ ] Write grant revocation, foreign grant, deletion, stale point, retry, and
  projection outage tests; observe RED.
- [ ] Implement control-plane operations under existing role guards.
- [ ] Run document and retrieval suites; expect PASS.
- [ ] Commit `add document access and lifecycle controls`.

### Task 6: Cited answers and safe query traces

Files: `backend/app/chat/contracts.py`, `provider.py`, `models.py`, `service.py`,
`api.py`; trace migration, config, router, API contract and chat tests.

Produces POST /chat/query and GET /query-runs/{id}. Bound questions to 4000
characters, results to 20, context to 16000 characters, and provider timeout to
30 seconds. Provider returns text plus cited chunk IDs. Validate every citation
against the context manifest. No evidence returns an abstention. A provider
failure records generation failure and exposes the safe trace ID.

- [ ] Write abstention, context bounds, false citations, timeout, trace
  redaction, and cross-tenant trace tests; observe RED.
- [ ] Implement an OpenAI-compatible adapter and deterministic test provider.
- [ ] Run chat and migration suites; expect PASS.
- [ ] Commit `add cited answers and safe query traces`.

### Task 7: Verification and handoff

Files: CI/verification entry points, README, generated OpenAPI/TypeScript.

- [ ] Add real Qdrant to CI and document separate worker/model setup.
- [ ] Regenerate API contracts; check migration head and absence of drift.
- [ ] Run the full backend, frontend, browser, and container gates.
- [ ] Review the complete diff using the post-code Engineering Playbook.
- [ ] Fix substantive findings, prove regressions, and commit.
- [ ] Commit `verify authorized document RAG`.

The document and chat UI is M3. Audit packs and benchmark comparisons remain
M4/M5. M2 is complete only when real-store authorization and lifecycle tests pass.
