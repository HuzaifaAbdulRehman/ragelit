# Isolation Comparison Implementation Plan

> For agentic workers: use superpowers:executing-plans task-by-task.

Goal: run three owned retrieval strategies through the same functional and audit
contracts without changing the application's default.

Architecture: introduce a typed store query boundary, then integrate tenant
routing with owned workspace snapshots. The lab baseline retains raw observations
before post-filtering. A separate command and report envelope preserve old packs.

Tech stack: existing Python, FastAPI, PostgreSQL, Qdrant and test tooling only.
Spec: docs/superpowers/specs/2026-10-05-isolation-comparison-design.md.
Execution: native implementation, focused TDD, one whole-branch review.

## Global constraints

- Default shared query filters, RRF, thresholds and limits remain unchanged.
- Existing configuration hashes, fixture checksums and binding schema stay intact.
- No new dependency, ordinary documents, paid call or production selector.
- Exact owned namespace inventory; no automatic adoption, deletion or reset.
- Same pinned 51-case inventory and scorer; literal release outcomes 0/0/1.
- Original bytes, no duplicates/raw content, replayed scoring, no overwrites.
- Keep every issue-23 primary metric; deterministic controls are not model quality.
- Reuse unchanged passing evidence; full milestone gate once after code is ready.
- Preserve the reviewed injection branch and all logs. Do not push while public.

## Review focus

- A second tenant must not reuse a mutable first-tenant collection.
- Missing/extra/foreign namespace collections must be rejected without cleanup.
- Access removal and replacement must route writes to the same physical store
  queried by chat, not a stale mirror.
- The lab must retain raw evidence and cannot hide exposure by post-filtering.
- Duplicate JSON keys and interrupted runs must not yield a validated safe result.

## Task 1: Typed query boundary

Files: modify backend/app/retrieval/store.py and service.py; create
backend/tests/unit/retrieval/test_store_search.py.

Consumes: Embedding, models.Filter and current refreshed scope/version allowlist.
Produces: SearchProjection(raw: tuple[models.ScoredPoint, ...],
accepted: tuple[models.ScoredPoint, ...]); QdrantChunkStore.search_points(
organization_id: UUID, versions: tuple[UUID, ...], vector: Embedding,
filters: models.Filter, limit: int) -> SearchProjection.

- [ ] Write real embedded-Qdrant tests for permitted evidence, foreign tenants,
  inactive points, sparse-only matches and empty version/invalid-limit rejection.
  Assert literal returned IDs, nonempty positives and exact raw/accepted identity.
- [ ] Run uv run --frozen pytest tests/unit/retrieval/test_store_search.py -q.
  Expected: missing query boundary.
- [ ] Move the exact existing SDK query into the store. Keep projection and raw
  observation order in AuthorizedRetriever, using returned accepted points.
- [ ] Run focused tests and existing API retrieval/observation checks. Expected:
  shared retrieval behavior unchanged. Check Ruff/mypy and affected callers.
- [ ] Commit add typed retrieval store search.

## Task 2: Owned tenant storage and snapshots

Files: create backend/app/audits/isolation_store.py; modify
backend/app/audits/workspace.py, seeding.py and retrieval/store.py;
create backend/tests/unit/audits/test_isolation_store.py and
backend/tests/integration/audits/test_isolation_workspace.py.

Consumes: Task-1 query boundary, existing IndexContext/lifecycle methods and
the owned organization-binding registry.
Produces: TenantCollectionStore(client: QdrantClient, config: AuditConfiguration,
*, dimension: int, organizations: Callable[[], tuple[UUID, ...]]) implementing
QdrantChunkStore's write/search contract; collection_names() -> tuple[str, ...]
and ensure_tenants(organizations: tuple[UUID, ...]) -> None on both stores.
AuditConfiguration.vector_strategy: shared_pre_filter|tenant_collections.

- [ ] Write tests that tenant A/B writes and searches stay in distinct collections,
  direct/group grants survive routing, replacement/deactivation affects only the
  intended tenant and unknown tenants are rejected. Assert actual point storage.
- [ ] Add real workspace checks for default hash/schema stability, three exact
  collections, reopen, missing/extra collection drift, point drift, pre-existing
  unowned names and interrupted seeding. Cleanup only the exact fixture resources.
- [ ] Run the new tests. Expected: missing strategy/workspace support.
- [ ] Implement routing delegates with unchanged filters. Add owned collection
  creation to organization seeding and qualify tenant snapshot point IDs.
  Validate namespace inventory and markers; preserve legacy shared snapshot keys.
- [ ] Run affected workspace/seeding and new tests; Ruff/mypy. Expected: passes.
- [ ] Commit add owned tenant collection routing.

## Task 3: Guarded post-filter baseline and common controls

Files: create backend/app/audits/isolation_lab.py; modify
backend/app/audits/target.py; create
backend/tests/unit/audits/test_isolation_lab.py,
backend/tests/integration/audits/test_isolation_contract.py and
backend/tests/integration/audits/test_isolation_targets.py.

Consumes: SearchProjection, current SQL versions, actual owned shared storage.
Produces: LabPostFilterStore(workspace: AuditWorkspace, *, lab: bool);
BundledAuditTarget(..., retrieval_store: QdrantChunkStore | None = None).

- [ ] Write guard tests for absent opt-in, production config, closed/unowned
  workspace, foreign client/collections and non-safe target profile. Assert no
  query/write before rejection.
- [ ] Add the same functional controls for all three strategies: organization,
  direct, group, foreign-tenant/forged-group denial, revocation, delete and replace.
  Run the same full 51-case scorer/inventory for each. Assert 0/0/1, complete
  coverage and lab first exposure retrieval_raw without context/output leakage.
- [ ] Run focused tests. Expected: missing baseline/target override.
- [ ] Remove authorization filters only in the guarded lab query, keep raw points,
  post-filter by org/active/eligible version, then use normal projection checks.
  Target overrides must use the same client and exact owned collection set.
- [ ] Run focused common controls and new target journeys plus affected existing
  target guard checks. Expected: passes; retain actual utility failures.
- [ ] Commit add guarded isolation comparison controls.

## Task 4: Comparison command and reproducible artifacts

Files: create backend/app/audits/isolation_cli.py, isolation_reports.py,
matching unit/integration tests and docs/verification/2026-10-05-isolation-comparison.md;
modify scripts/verify.sh, .github/workflows/ci.yml and README.md.

Consumes: existing CLI configuration/metadata/quiet helpers, prepare_pack,
execute_cases, score_case/build_report, and Tasks 1..3.
Produces: python -m app.audits.isolation_cli --strategy
shared_pre_filter|tenant_collections|lab_post_filter [--lab];
IsolationReport(strategy, collections, cases: tuple[AuditCase, ...],
audit: AuditReport), strict original-byte writer and single/three-report validators.

- [ ] Write unit tests for literal strategy gates, exact cases, replayed tampered
  status/count rejection, duplicate keys, raw markers, bounded input, no overwrite,
  runtime/partial evidence and fixed redacted CLI errors.
- [ ] Add real subprocess journeys and separate exports using
  RAGELIT_ISOLATION_EXPORT_DIRECTORY. Verify provenance and exact collection counts
  1/3/1; the lab must require --lab before opening the workspace.
- [ ] Run focused tests. Expected: missing command/report behavior.
- [ ] Implement without changing old CLI readers or validators. Pin logical
  cases to generate_fixtures and replay score_case/build_report. Use existing
  bounded no-link reader and atomic publisher. Reject duplicate keys in originals.
- [ ] Run focused tests, Ruff/mypy and secret scan. Validate saved real artifacts.
  Add a separate fresh CI destination, validator gate and artifact upload.
- [ ] Humanize short docs and commit add reproducible isolation comparison.
- [ ] One independent whole-branch review; Important/Critical fixes get RED/GREEN.
  Only push/PR/merge after private visibility is resolved and current-head gates
  pass. Keep issues 23/24 and the missing control matrix in the full goal.
