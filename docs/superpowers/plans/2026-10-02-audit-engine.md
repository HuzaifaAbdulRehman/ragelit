# M4 Audit Engine Implementation Plan

Execution: use `superpowers:executing-plans` task by task with TDD.
The user has delegated design and execution choices; proceed inline without
additional approval pauses. Preserve local commits and do not push.

**Goal:** Run a complete synthetic access-control audit against RAGelit and
export bounded evidence with reliable exit codes.

**Architecture:** A local runner validates and locks an owned audit workspace,
seeds fixtures through application services, and invokes the normal API
in-process. A trusted observer captures stage facts. The scorer checks the
versioned inventory and manifests independently of the target.

**Tech Stack:** Python 3.12/3.13, Pydantic 2, FastAPI, SQLAlchemy 2, PostgreSQL,
Qdrant, pytest, existing uv lock, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-02-audit-engine-design.md`

## Global constraints

- Synthetic, local/test targets only; no ordinary uploaded documents.
- Database and collection names start with `ragelit_audit_`.
- Explicit audit configuration; never inherit the app's target/provider.
- Application cases use the non-owner role and forced RLS.
- Raw canaries, content, prompts, tokens, passwords, URLs, and provider headers
  stay out of reports and diagnostics.
- No paid model, new runtime dependency, external target, or audit web API.
- Keep unsafe adapters out of the normal web dependency graph.
- At most 20 chunk IDs and 20 canary-match records per boundary.
- Missing evidence/cases return 2; complete failures return 1; complete passes 0.
- Positive controls prevent deny-all from passing.
- Windows/Linux share inventory, template checksum, and decisions.

## Review focus

- A forged result changes its expected fixture IDs: scoring uses the case,
  not target assertions. Task 1 tests forbidden evidence independently.
- A provider returns an answer but citation validation rejects it: candidate
  output is observed without claiming delivery. Task 2 covers containment.
- Workspace paths traverse or link outside their root: reject before mutation.
  Task 4 covers resolved-path and ownership failures.
- A second run starts during lifecycle changes: reject with a bounded error.
  Task 4 tests workspace lock contention.
- Report writing fails after successful cases: exit 2, never print unsafe
  diagnostics. Task 6 tests a failing destination.

## File responsibilities

- `backend/app/audits/contracts.py`: immutable validated case/evidence/result
  types and protocol definitions.
- `backend/app/audits/scoring.py`: independent case and inventory scoring.
- `backend/app/audits/reports.py`: safe report metadata, serialization, atomic
  persistence, and checksum receipt.
- `backend/app/audits/observer.py`: in-memory exact matching and stage facts.
- `backend/app/observability.py`: narrow optional observation protocol used
  by shipped services, with no import of audit/lab code.
- `backend/app/audits/fixtures.py`: fixed-seed logical template and checksum.
- `backend/app/audits/embeddings.py`: deterministic fixture vector mapping.
- `backend/app/audits/workspace.py`: explicit settings, identity/ownership
  guards, manifest bindings, and database lock.
- `backend/app/audits/seeding.py`: actor bootstrap and normal document/worker
  calls for owned fixtures.
- `backend/app/audits/target.py`: safe in-process target and case actions.
- `backend/app/audits/lab.py`: explicit guarded broken Qdrant adapters.
- `backend/app/audits/cli.py`: CLI, full-pack runner, safe diagnostics.
- `backend/tests/unit/audits/`: pure contracts/scoring/fixtures/report tests.
- `backend/tests/api/test_audit_observation.py`: real chat boundary evidence.
- `backend/tests/integration/audits/`: owned local workspace and full packs.

## Task 1: Audit contracts, scorer, and safe reports

**Files:** Create contracts/scoring/reports and the audit package. Tests live
in `backend/tests/unit/audits/test_scoring.py` and `test_reports.py`.

**Interfaces:**

- Produce `AuditCase`, `AuditObservation`, `AuditCaseResult`, `AuditReport`,
  `RunMetadata`, and `AuditTarget.execute(case: AuditCase) -> AuditObservation`.
- Produce `score_case(case: AuditCase, observation: AuditObservation)
  -> AuditCaseResult`.
- Produce `build_report(cases: tuple[AuditCase, ...],
  results: tuple[AuditCaseResult, ...], metadata: RunMetadata) -> AuditReport`.
- Produce `write_report(report: AuditReport, directory: Path) -> Path`.
- Cases pin expected HTTP status, positive required chunks, forbidden
  chunks/canary IDs, required boundaries, and an optional registered
  citation-challenge ID. Observations never contain raw text.
- Boundaries distinguish raw/accepted retrieval, provider context,
  candidate/delivered output, and candidate/delivered citations.
- Each boundary records observed/not_reached/unobserved plus sequence.
  Not-reached evidence requires an explicit terminal decision.

- [ ] Write scorer tests. Removing forbidden-ID checks must fail
  `test_forbidden_retrieval_fails_after_containment`; treating missing evidence
  as empty must fail `test_missing_stage_is_inconclusive`.
- [ ] Add positive, deny-all, unexpected-status, early-denial, invalid-citation
  containment, truncation, duplicate/missing/unknown inventory, and failure
  with incomplete-coverage tests. Assert literal pass/fail/inconclusive and
  exit-code values 0/1/2.
- [ ] Add report tests: unsafe fields rejected, no text/canary values in JSON,
  atomic non-overwriting persistence, matching SHA-256 receipt.
- [ ] Run `uv run pytest tests/unit/audits -q`; expect RED for missing audit
  functionality, not malformed test fixtures.
- [ ] Implement those exact contracts, validation, scorer, and report functions.
  Use fixed enums for errors/reasons and frozen Pydantic models with extra
  fields forbidden. Validate unique case/stage sequences and finite timings.
- [ ] Run the task tests, Ruff, mypy, and existing unit gate; expect GREEN.
- [ ] Commit `Add audit scoring and redacted reports`.

## Task 2: Observe actual chat and retrieval boundaries

**Files:** Create observability/observer. Modify
`backend/app/chat/service.py`, `chat/api.py`, and
`backend/app/retrieval/service.py`. Create
`backend/tests/api/test_audit_observation.py`.

**Interfaces:**

- Consume Task 1 case/evidence types.
- Produce `AuditObserver` with read-only stage recording and an observation
  snapshot. Canary registry belongs in its memory, not serialization.
- Services accept a keyword-only optional observation sink, default None.
  API reads an explicitly installed trusted sink from application state.
  Request models expose no new observer or scope fields.
- Sink callbacks observe raw Qdrant payloads before validation, accepted
  chunks, actual provider context, candidate output/citations, delivered
  output/citations, and safe terminal outcome.

- [ ] Write tests for raw forbidden Qdrant output followed by projection
  rejection, valid positive stage coverage, provider timeout, no-evidence
  abstention, candidate output with rejected citations, and observer failure.
  Removing the raw callback must make the first case inconclusive.
- [ ] Run those API tests; expect RED for missing boundary evidence.
- [ ] Implement optional hooks without changing filters or ordinary response/
  trace contracts. Sequence observations in actual execution order.
- [ ] Run new API tests plus chat/retrieval tests and existing unit gate;
  expect GREEN. Verify OpenAPI does not change.
- [ ] Commit `Observe audit evidence at chat boundaries`.

## Task 3: Generate reproducible synthetic fixtures

**Files:** Create fixtures/embeddings. Tests:
`backend/tests/unit/audits/test_fixtures.py`.

**Interfaces:**

- Produce `generate_fixtures(seed: int = 20261002) -> FixtureTemplate`,
  its canonical checksum, and versioned required logical case inventory.
- Produce `FixtureEmbeddings` implementing existing `EmbeddingProvider`.
- Template contains three organizations, four groups each, reserved actors,
  all specified lifecycle/document classes, exact canaries, relevance labels,
  and queries. Runtime bindings are separate.

- [ ] Write tests asserting three organizations/four groups, stable checksum,
  different-seed change, no credentials, unique canaries, and all required
  control groups. Ranking tests must prove forbidden probe vectors outrank
  permitted anchor vectors under an unfiltered query.
- [ ] Run fixture tests; expect RED for missing generator/embedding functions.
- [ ] Implement fixed-seed local generation, canonical JSON hashing, and
  nonconstant deterministic dense/sparse fixture embeddings.
- [ ] Run fixture tests and all audit/unit tests; expect GREEN.
- [ ] Commit `Generate deterministic audit fixtures`.

## Task 4: Guard and seed an owned workspace

**Files:** Create workspace/seeding. Tests:
`backend/tests/unit/audits/test_workspace.py`,
`backend/tests/integration/audits/test_workspace.py`.

**Interfaces:**

- Consume Task 3 template and existing migrations, identity/document services,
  storage helpers, `run_once`, and application-role session factory.
- Produce `AuditWorkspace` as a context manager with explicit local target
  configuration, ownership marker, nonblocking database lock, and validated
  bindings.
- Produce `seed_workspace(workspace: AuditWorkspace,
  template: FixtureTemplate) -> FixtureBindings`.
- Binding writes are checksummed/atomic; lifecycle instances are keyed by
  run/case and cannot alter the reusable base fixtures.

- [ ] Write guard tests for production/remote/ordinary targets, unsafe resolved
  paths, unknown rows/points, marker drift, and concurrent lock failure.
- [ ] Add real-service tests for forced application-role RLS, normal ingestion,
  idempotent repeat seed, interrupted-run evidence, and owned-only cleanup.
- [ ] Run unit guards and integration workspace tests; expect RED.
- [ ] Implement ownership validation before every mutation. Bootstrap only an
  empty dedicated target; never reset unknown resources or change an existing
  unrelated database role. Use a workspace-specific application role.
- [ ] Seed actors and groups, then upload/grant/ingest through existing services.
  Require ready state and a permitted control query for probe documents.
- [ ] Run those tests plus existing migration/tenancy/document tests;
  expect GREEN.
- [ ] Commit `Seed isolated audit workspaces safely`.

## Task 5: Execute the full access-control pack

**Files:** Create target/lab. Tests:
`backend/tests/integration/audits/test_targets.py`.

**Interfaces:**

- Consume Tasks 1 through 4.
- Produce `BundledAuditTarget.execute(case: AuditCase) -> AuditObservation`
  and binding-to-case expansion for the frozen required inventory.
- Safe profile uses the real API, login, current membership, RLS, retriever,
  lifecycle services, observer, fixture embeddings, and deterministic provider.
- Lab adapters remove filters or deny all only after explicit lab opt-in and
  workspace ownership checks; never import them from web runtime modules.

- [ ] Write full-pack safe/vulnerable/deny-all tests. Assert literal gate codes
  0/1/1, complete coverage, and retrieval first exposure in the vulnerable run.
- [ ] Cover stale sessions after membership/group/grant changes, deleted and
  replaced versions, role/read separation, metadata spoofing, and citation
  rejection. Verify the new version's positive control.
- [ ] Run target tests; expect RED.
- [ ] Implement case-specific setup/actions with isolated lifecycle fixtures.
  Never mutate expected labels using target results. Authenticate every actor
  and execute as the application role.
- [ ] Run all audit tests and existing API/integration tests; expect GREEN.
- [ ] Commit `Run access-control audits through the API`.

## Task 6: Ship the CLI and CI gate

**Files:** Create CLI, tests `backend/tests/unit/audits/test_cli.py` and
`backend/tests/integration/audits/test_cli.py`. Modify CI and verification
scripts; add a short usage section to README.

**Interfaces:**

- Consume the full runner and report writer.
- Produce `main(argv: list[str] | None = None) -> int`, invoked with
  `python -m app.audits.cli`.
- Default full safe pack; broken profiles require `--lab`. Partial selection,
  invalid configuration, service failure, and report-write failure return 2.
- Require explicit audit target environment/configuration. No raw configuration,
  stack trace, or provider credentials in CLI output.

- [ ] Write subprocess tests for exact exit codes, parseable report/receipt,
  partial runs, broken profiles without opt-in, unavailable services, and
  destination failure. Scan captured output for known synthetic sensitive data.
- [ ] Run CLI tests; expect RED.
- [ ] Implement bounded diagnostics and report persistence with reliable failure
  precedence. Preserve confirmed exposures in incomplete reports.
- [ ] Add CI safe gate and separately asserted vulnerable/deny-all regressions.
  Validate redaction before uploading report artifacts.
- [ ] Run full `scripts/verify.ps1` on Windows and equivalent backend/audit gates
  on Linux. Expect all existing tests and new audit controls GREEN.
- [ ] Commit `Add the audit CLI and CI release gate`.

## Final review and handoff

- [ ] Run secret/dependency checks and inspect tracked files.
- [ ] Use one fresh whole-branch reviewer after all task commits. Fix important
  findings with RED/GREEN regressions and a green full suite.
- [ ] Record verified commands, test counts, run/report checksums, remaining
  limits, and any deferred minor findings.
- [ ] Keep the feature branch/worktree local. No push, merge, or public release
  is implied by this plan.
