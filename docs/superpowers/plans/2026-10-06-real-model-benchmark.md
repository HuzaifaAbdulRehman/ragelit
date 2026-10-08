# Real-model benchmark implementation plan

> For agentic workers: use `superpowers:executing-plans` inline. The user has
> authorized routine technical decisions; do not repeat approval menus.

**Goal:** Run the original M5.2 benchmark through the production application
with pinned local models and replayable results.

**Architecture:** Verify immutable model assets before offline loading. Extend
the existing owned workspace and seeding path, then collect bounded query and
cost records and replay summaries without services.

**Tech stack:** Existing Python, FastEmbed 0.8.1, PostgreSQL, Qdrant, FastAPI.

**Spec:** `docs/superpowers/specs/2026-10-06-real-model-benchmark-design.md`

## Global constraints

- No dependencies, paid calls, credential-cache access, or shared settings.
- Corpus `natural-utility-v1`, seed `20261005`; retain every original metric.
- Dense 384-dimensional BGE, BM25 English/IDF, two CPU threads; reranker `none`.
- Generation: temperature 0, 1024 tokens, JSON, existing 30-second deadline.
- No security relaxation, quality-filtered seeding, invented results, or pushes
  while the repository is public. Preserve logs and resumable workspaces.

## Review focus

- A changed tokenizer or stopword file must fail before model construction.
- Reopening a workspace under different embeddings must reject before mutation.
- A poor retrieval answer must be measured, not excluded by a seed probe.
- Missing, duplicate, or interrupted queries must not improve aggregates.
- Cross-strategy pairs and security denominators must share proven provenance.

### Task 1: Pinned offline embedding provider

Files: create `backend/app/evaluation/models.py`, `embedding-pins.json`,
`backend/tests/unit/evaluation/test_models.py`; document pin reproduction.

Consumes: production `FastEmbedProvider.documents(texts)` and `.query(text)`.
Produces: `verify_embedding_assets(root: Path) -> EmbeddingPins` and
`PinnedEmbeddingProvider(root: Path)`, with `.fingerprint: str`.

- [ ] Write asset tests for exact hashes/sizes/inventory, missing or changed
  files, relative or linked paths, and version mismatch; use real tiny files.
- [ ] Run focused tests and verify behavioral RED before implementation.
- [ ] Implement bounded verification and offline initialization with explicit
  dense/sparse paths and settings. Keep inherited production methods unchanged.
- [ ] Run focused tests, Ruff/mypy, and staged secret scan; commit.
- [ ] Download the pinned public files to ignored local storage and run real
  document/query embeddings with network disabled. Record versions/checksums.

### Task 2: Real embeddings in the owned application workspace

Files: modify `audits/workspace.py`, `audits/fixtures.py`, `audits/seeding.py`;
create `evaluation/workspace.py` and focused workspace/seeding tests.

Consumes: Task 1 provider/fingerprint and the committed `UtilityCorpus`.
Produces: owned workspace with checksum-bound embeddings and utility seed
adapter, keeping the default deterministic audit path unchanged.

- [ ] Write RED tests for bound provider identity/geometry, reopen drift,
  same ingestion/retrieval provider, and bad retrieval preserved after seeding.
- [ ] Implement keyword-only provider injection and conditional configuration
  fingerprint. Add an explicit utility template/seed mode with no quality probe.
- [ ] Run affected guard/ownership/seeding tests and static checks; commit.

### Task 3: Benchmark runner and offline result replay

Files: create `evaluation/runner.py`, `reports.py`, `cli.py` and focused tests.

Consumes: Task 2 workspace, corpus qrels, existing metric helpers, audit packs,
compatible generation provider, and strategy stores.
Produces: `python -m app.evaluation.cli` plus per-query reports and a validator.

- [ ] Write RED tests for distinct-document rankings, literal metric oracles,
  citation correctness, partial failures, missing/duplicate queries, redaction,
  strategy pairing, and cost/revocation measurement boundaries.
- [ ] Implement production chat observations, fresh per-query authentication,
  bounded raw artifacts, all primary summaries, and seeded paired intervals.
  Bind corpus, models, generation, source, machine, and service provenance.
- [ ] Run focused tests and a short production-path integration; commit.

### Task 4: Measured cohort and release evidence

Files: benchmark method/results documentation and existing release checklist.

Consumes: Task 3 CLI and validator with verified owned local services/models.
Produces: actual strategy results, complete coverage or explicit missing data,
one independent review, and reusable current-head verification evidence.

- [ ] Confirm an awake window before long service-backed checks; prove the
  local generation server's loaded weights, rather than trusting a declaration.
- [ ] Run the fixed cohort and security packs, collect all costs and stages,
  validate original artifacts offline, and report negative results unchanged.
- [ ] Review the completed integration once. Fix important findings with
  RED/GREEN tests; defer nonessential improvements.
- [ ] Run the milestone gate once, document results/limits, and commit.
- [ ] Deliver through private GitHub only after privacy is confirmed; then
  continue the remaining clean-clone/demo work from the original roadmap.
