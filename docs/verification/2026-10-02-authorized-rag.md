# Authorized RAG verification

Verified on 2 October 2026 in the `feat/authorized-rag` worktree.
The backend milestone is implemented. Document upload and chat screens,
privacy audits, and retrieval benchmarks still belong to M3 through M5.

## Checks that passed

- `uv run pytest tests -q --tb=short`, from `backend`: 202 tests passed
  in 255.10 seconds. The command exited with status 0.
- Ruff formatting and lint checks passed; mypy passed for 100 source files.
  No Python LSP server was configured, so these are not LSP diagnostics.
- `uv sync --frozen --project backend` accepted the lockfile unchanged.
- Frontend format and type checks, production build, and generated API client
  checks passed. Regenerating the client produced no diff.
- All four Playwright authentication and tenant-navigation journeys passed
  in 26.7 seconds against a fresh database upgraded through migration 0006.
- Compose configuration passed. Real integration tests used PostgreSQL 16
  and Qdrant 1.15.4 on this Windows laptop.

## Review fixes

The independent branch review found two critical issues, three important
issues, and one minor issue. Each substantive issue was reproduced with a
failing regression test before its fix:

- An obsolete projection-repair job could republish an old document version.
  Publication now checks the intended version, and replacement cancels old jobs.
  A composite foreign key binds that version to the same document and tenant.
- Provider redirects could forward the configured key and read an unbounded
  response. The adapter rejects redirects before reading the body.
- Slow response bodies could exceed the configured request timeout. One deadline
  now covers the whole HTTP request, including streaming the response.
- PDF pages could accumulate decoded content beyond the per-stream cap. Parsing
  now runs in a child process with a 256 MiB memory cap and a 30-second timeout.
  Main page content also has a cumulative 16 MiB decoding budget. A separate
  process test confirmed that the Windows memory limit rejects a large allocation.
- Existing Qdrant collections could pass validation with incompatible settings.
  Validation now checks cosine distance, sparse IDF, payload index types, and
  the tenant-index flag before accepting an existing collection.

## Decisions and remaining limits

- The intended-version pointer costs another migration and control-plane update.
  It avoids timestamp ambiguity and stale publication when content repeats.
- Bounded PDF parsing adds process startup overhead and can reject complex valid
  PDFs. Per-stream caps alone cannot bound form and resource allocations.
- M3 through M5 retain the document/chat UI, audits, and benchmarks. There is
  no upload/chat browser journey yet.
- Fixtures verify contracts, not real-model quality or prompt-injection
  resistance. Neither has been measured, so there are no empirical CV claims yet.
- Soft deletion retains source files and inactive vectors for recovery.
  A purge mechanism remains future work; deletion is not data erasure.
- Local tests use PostgreSQL 16. PostgreSQL 18 remote CI has not been verified.
  Online OSV scanning was blocked because it sends private dependency metadata;
  the offline scanner had no vulnerability databases. Compatibility problems or
  dependency vulnerabilities may still be undiscovered.

One minor finding is deferred: grant editing accepts an active membership for a
globally inactive user. Retrieval denies that user, but the grant may take effect
if the user is reactivated.

No real LLM or paid API was used for verification. The public local embedding
models passed a separate smoke check. This is not a production certification.
