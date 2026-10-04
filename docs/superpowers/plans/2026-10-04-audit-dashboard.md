# Audit Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let authorized portal users start a synthetic audit, inspect its saved evidence, and download validated JSON.

**Architecture:** PostgreSQL stores tenant-scoped jobs and original reports. The API only queues and reads jobs; a separate worker invokes the existing CLI in an owned fixture namespace. React uses the existing workspace cancellation and query cache.

**Tech Stack:** Existing Python/FastAPI, SQLAlchemy/Alembic, PostgreSQL, React, TypeScript, TanStack Query/Router, and Playwright. No new runtime dependencies.

**Spec:** [Approved dashboard design](../specs/2026-10-04-audit-dashboard-design.md).

## Global Constraints

- Run only the complete 51-case safe profile. Broken profiles remain CLI-only.
- Invented fixtures live in separate owned loopback databases/collections, never company uploads or the application database.
- Owner/admin/auditor use `Action.AUDITS_RUN`; members cannot access any audit route, including downloads.
- Preserve the original validated UTF-8 report within the existing 8 MiB limit. Request UUID and report UUID remain distinct.
- Bound execution to 1800 seconds. Poll visible queued/running jobs every two seconds; never invent percentage progress.
- No injection pack, HTML export, benchmarks, streaming, paid provider, automatic purge, or visibility change in this slice.
- Use focused RED/GREEN checks, meaningful local commits, one final branch review, and one relevant final gate. Reuse unchanged engine evidence.
- Native execution was approved on 4 October 2026. Implementation is committed locally through 0cbd52f. Final verification is incomplete: Windows ran out of memory, and the real audit journey returned an inconclusive partial report. Resume from the retained progress ledger after freeing memory; do not repeat the passing suites.

## Review Focus

1. A modified browser sends target/profile/path fields: reject them before any worker side effect (Task 1).
2. Two tabs start simultaneously: persist one outstanding job and return one 409, not two fixture runs (Task 1).
3. A valid non-ASCII report is downloaded: preserve checked bytes and SHA-256 rather than reserialize JSON (Tasks 1 and 4).
4. A laptop sleeps beyond the lease: mark recovery required and reject a stale claimant's result (Task 3).
5. A role or organization changes during polling/download: clear private state and discard late responses (Task 4).

## File map

Create `backend/app/audit_jobs/`:

- `models.py` owns `AuditRun`; `schemas.py` owns API DTOs.
- `api.py` registers routes; `service.py` authorizes enqueue/read/download.
- `contracts.py` defines worker-only data records without privileged imports.
- `execution.py` owns explicit bootstrap configuration and the CLI subprocess boundary.
- `claims.py` owns transactional claim/lease/recovery transitions.

Create `backend/app/workers/audit.py` for the operator command. Modify the existing router, Alembic registry, and generated OpenAPI contracts. Add migration `backend/app/alembic/versions/0007_audit_runs.py`.

Create `frontend/src/api/audits.ts` and `frontend/src/features/audits/{AuditsPage,AuditDetailPage,AuditEvidence}.tsx`. Modify `frontend/src/api/client.ts`, the existing router, and AppShell. Extend existing browser fixture tooling only where the real worker journey needs it.

## Task 1: Protected storage and API

**Files:** Create the model/DTO/service/API files above and package initializer. Modify `backend/app/api/router.py`, `backend/app/alembic/env.py`, `backend/tests/unit/audits/test_lab.py`, `backend/openapi.json`, and `frontend/src/api/generated/schema.ts`. Add `backend/tests/api/test_audits.py`, `backend/tests/integration/audit_jobs/test_storage.py`, and test package initializers; extend `backend/tests/test_migration_head.py`.

**Interfaces:** The service consumes `RequestPrincipal`, `Session`, and `Action.AUDITS_RUN`. Define:

```python
enqueue_audit(principal: RequestPrincipal, *, session: Session) -> AuditRun
list_audits(principal: RequestPrincipal, *, session: Session, limit: int = 20, offset: int = 0) -> AuditRunList
get_audit(run_id: UUID, principal: RequestPrincipal, *, session: Session) -> AuditRun
summarize_run(run: AuditRun, *, now: datetime | None = None) -> AuditRunSummary
download_report(run: AuditRun) -> bytes
```

`AuditRunCreate` is empty with extras forbidden. `AuditRunSummary` carries request ID, timestamps, state (`queued/running/finished/recovery_required`), outcome (`unknown/pass/fail/inconclusive`), optional exit/error, and report availability/UUID/SHA. `AuditRunList` carries items, total, limit, offset. `AuditRunDetail` carries summary plus `AuditReport | None`; no claim token, root, or bootstrap settings.

- [ ] Add API tests `test_every_audit_route_requires_current_privileged_role`, `test_foreign_run_is_not_found`, `test_start_rejects_execution_fields`, and `test_download_preserves_utf8_bytes`. Parameterize all four roles and all routes. Assert allowed reads, member 403, foreign ID 404, extra fields 422, and download bytes/hash equal the literal seeded artifact. Test role loss/deactivation with the same access token.
- [ ] Add real application-role tests `test_audit_runs_force_rls`, `test_requester_cannot_reference_another_tenant`, `test_concurrent_start_admits_one_run`, and migration round-trip/drift tests. Assert missing context exposes no rows, foreign inserts fail, and two separate connections produce one accepted row and one conflict. Include a recovery-required row in admission blocking.

  ```python
  assert extra_response.status_code == 422
  assert sorted(response.status_code for response in concurrent_starts) == [202, 409]
  assert download.content == original_utf8_bytes
  assert hashlib.sha256(download.content).hexdigest() == saved_sha256
  ```

- [ ] Run from `backend`: `uv run --frozen pytest tests/api/test_audits.py tests/integration/audit_jobs/test_storage.py tests/test_migration_head.py -q`. Expected RED for missing routes/table, then behavioral assertions as each component is added.
- [ ] Implement the table with tenant-consistent requester foreign keys, FORCE RLS, original report text/hash, bounded `octet_length`, and state/report consistency constraints. A partial unique index covers queued/running/recovery_required rows. Keep model registration consistent with the audit workspace's exact table snapshots.
- [ ] Implement POST/GET routes under `/api/v1/audits`, newest-first pagination (`limit` 1..100, `offset` >=0), 202 enqueue, fixed-code duplicate 409, not-ready 409, and corrupt stored-report 503. Check current role every time. Downloads recheck stored SHA and report schema and return original bytes. Expired running leases project recovery-required/inconclusive without mutating on GET.
- [ ] Replace the blanket audit-package import ban with a transitive web-boundary guard rooted at `app.main`, `app.api.router`, and `app.export_openapi`. Allow shared report/contracts/scoring only; forbid reachable CLI, runner/target, workspace, seeding, fixtures, and lab modules. Test direct, relative, and re-export edges plus a fresh-process web import. Privileged worker imports are outside the web roots; do not disable the guard.
- [ ] Rerun the scoped tests and guard: `uv run --frozen pytest tests/api/test_audits.py tests/integration/audit_jobs/test_storage.py tests/test_migration_head.py tests/unit/audits/test_lab.py -q`. Require zero failures. Run `uv run --frozen python -m app.export_openapi`; from `frontend`, run `npm.cmd run generate:api` and `npm.cmd run check`.
- [ ] Commit these files and generated contracts: `add protected audit job endpoints`.

## Task 2: Fixed CLI execution bridge

**Files:** Create `backend/app/audit_jobs/contracts.py`, `execution.py`, and `backend/tests/unit/audit_jobs/test_execution.py` with package initializers.

**Interfaces:** `AuditClaim` has request/organization/requester/claim UUIDs and `lease_until: datetime`. `CliOutcome` has `exit_code: Literal[0, 1, 2]`, optional report content/UUID/SHA, optional fixed error code, and `recovery_required: bool`. Neither record imports the engine workspace.

```python
configuration_from_environment() -> AuditWorkerConfiguration
workspace_configuration(configuration: AuditWorkerConfiguration, request_id: UUID) -> AuditConfiguration
run_cli(configuration: AuditWorkerConfiguration, request_id: UUID, *, heartbeat: Callable[[], bool]) -> CliOutcome
```

`AuditWorkerConfiguration` contains only explicit audit environment, loopback admin/Qdrant URLs, root, application password, fixture password, and bounded timeout. These values are worker-only, never API DTOs.

- [ ] Add `test_cli_uses_fixed_safe_command_and_clean_environment`, `test_invalid_artifact_cannot_pass`, and `test_timeout_reaps_child_before_return`. Exercise the actual subprocess boundary with small test child programs; doubles may replace external execution but not artifact validation. Assert no shell/lab/case/client arguments, no ordinary provider/authentication secrets, safe-profile validation, and literal exit 2 for malformed diagnostics, oversized output, hash mismatch, wrong report UUID/profile, or inconsistent exits.
- [ ] Add heartbeat-loss and cleanup-failure cases. Assert a stale lease stops/reaps the owned child; inability to confirm shutdown sets recovery required. Test missing explicit settings and existing loopback/prefix/path/junction guards without touching ordinary stores.

  ```python
  assert malformed_artifact_outcome.exit_code == 2
  assert malformed_artifact_outcome.report_content is None
  assert unconfirmed_shutdown_outcome.recovery_required is True
  ```

- [ ] Run `uv run --frozen pytest tests/unit/audit_jobs/test_execution.py -q`; expected RED for missing bridge.
- [ ] Derive `ragelit_audit_` plus the request UUID hex for database/collection and matching directory beneath the configured root. Retain effective libpq address/timeouts and ownership checks. Launch `[sys.executable, '-m', 'app.audits.cli']`, with a platform-essential environment allowlist and explicit audit variables. Bound stdout and stderr to 64 KiB each, never persist raw output, and enforce the 1800-second cap.
- [ ] Validate canonical diagnostic/report IDs, the owned report path/receipt, and `app.audits.artifacts.validate_report`. Require safe profile and agreement between all three exits. Accept valid partial evidence as inconclusive; missing or corrupt artifacts never pass. Stop, then kill if needed after five seconds, and reap before returning. Unconfirmed cleanup requires recovery.
- [ ] Rerun the bridge tests and existing CLI/artifact unit regressions. Expected zero failures. Commit: `bridge audit jobs to the safe CLI`.

## Task 3: Worker leases and explicit recovery

**Files:** Create `backend/app/audit_jobs/claims.py`, `backend/app/workers/audit.py`, `backend/tests/unit/audit_jobs/test_worker.py`, and `backend/tests/integration/audit_jobs/test_claims.py`.

**Interfaces:** Consume Task 2 records/bridge and Task 1 model. Define:

```python
claim_audit(org_id: UUID, *, session: Session, now: datetime | None = None) -> AuditClaim | None
renew_audit_claim(claim: AuditClaim, *, session: Session, now: datetime | None = None) -> bool
finish_audit_claim(claim: AuditClaim, outcome: CliOutcome, *, session: Session, now: datetime | None = None) -> bool
mark_recovery_required(claim: AuditClaim, *, session: Session, now: datetime | None = None) -> bool
resolve_interrupted_audit(run_id: UUID, org_id: UUID, *, session: Session, worker_stopped_confirmed: bool) -> None
run_once(org_id: UUID, *, session_factory: Callable[[], Session], configuration: AuditWorkerConfiguration) -> bool
```

- [ ] Add `test_revoked_requester_never_executes`, `test_stale_claim_cannot_publish`, `test_global_lock_serializes_healthy_workers`, and `test_expired_lease_requires_explicit_recovery`. Assert no execution for inactive/disallowed requesters, zero stale-CAS updates, one healthy executor across organizations, no automatic replay, and rejection of recovery without explicit confirmation or matching tenant/run.

  ```python
  assert finish_audit_claim(stale_claim, outcome, session=session, now=after_lease) is False
  assert persisted.state == "recovery_required"
  assert persisted.outcome == "inconclusive"
  ```

- [ ] Run `uv run --frozen pytest tests/unit/audit_jobs/test_worker.py tests/integration/audit_jobs/test_claims.py -q`; expected RED for missing lifecycle functions.
- [ ] Claim under the existing scoped database context and a deployment-wide advisory lock. Recheck requester activation/membership/action immediately before execution. Release transactions before external work. Renew a 60-second lease every 15 seconds. Atomically persist state/outcome/report only for the current running, unexpired claim; retain known failures in valid partial artifacts.
- [ ] Support `python -m app.workers.audit --organization-id UUID [--once]`. The same operator command accepts `--recover-run UUID --confirm-worker-stopped`, mutually exclusive with normal execution. Recover only an interrupted/recovery-required matching job, retain its fixtures, and finish inconclusively. No target/profile/path flags, purge, or reset. Startup notices expired jobs rather than replaying them.
- [ ] Prepare operator worker wiring for Task 4's real browser journey. That journey queues through the actual API, runs the real worker against dedicated PostgreSQL/Qdrant fixtures, and asserts 51 cases, safe exit 0, a distinct report UUID, and exact downloaded artifact bytes/SHA. Execute the full pack once per platform, not again in a duplicate backend journey. No paid model or mocked engine is allowed for that proof.
- [ ] Rerun unit and claim integration tests; require zero failures. Commit: `run leased audits with explicit recovery`.

## Task 4: Portal flow and final slice gate

**Files:** Create the frontend API/pages from the file map and `frontend/tests/{audits,audits-scope}.spec.ts`. Modify existing router/AppShell, `frontend/src/api/client.ts`, `backend/tests/e2e_seed.py`, `backend/tests/e2e_app.py`, `frontend/tests/start-api.mjs`, and README. Add `frontend/tests/audit-worker-journey.spec.ts` and `docs/verification/2026-10-04-audit-dashboard.md` when verified.

**Interfaces:** Define `auditsApi.start(token: string, signal: AbortSignal): Promise<AuditRunSummary>`, `.list(token: string, limit: number, offset: number, signal: AbortSignal): Promise<AuditRunList>`, `.detail(token: string, id: string, signal: AbortSignal): Promise<AuditRunDetail>`, and `.download(token: string, id: string, signal: AbortSignal): Promise<Blob>`. DTOs come from Task 1 generated types. Add `requestBlob(path: string, init?: RequestInit, token?: string): Promise<Blob>` with the existing request credentials/error policy; do not change `request<T>` callers. Pages consume `useWorkspace()` and its tenant/revision key and cancellation signal.

- [ ] Add browser tests for role navigation, queued/running/finished/recovery states, reportless errors, exact 0/1/2 mapping, hostile text, UTF-8 downloads, and late results after role loss/tenant switch. Assert no script execution, old private content disappears, polling stops after 401/403, and downloads contain server bytes rather than serialized view data.

  ```typescript
  await expect(page.locator("script[data-audit]")).toHaveCount(0)
  await expect(page.getByText(oldTenantCaseId)).toHaveCount(0)
  expect(downloadedBytes.equals(validatedArtifactBytes)).toBe(true)
  ```

- [ ] Run `npm.cmd run test -- audits.spec.ts audits-scope.spec.ts`; expected RED for missing screens. Follow existing role checks and components. Enable Audits only for allowed roles; show fixed errors and synthetic/fused-retrieval scope notices. Render stage values as text with candidate/delivered and observed/not-reached/unobserved distinctions and event-sequence first exposure.
- [ ] Poll at two seconds only while visible and queued/running; stop on terminal/recovery, denial, unmount, logout, role loss, and tenant switch. Mutations never auto-retry. Abort download on scope loss; revoke temporary object URLs. Do not expose old cached results after permission denial.
- [ ] Wire `test_queued_job_matches_real_safe_cli_artifact` as the single real browser start-to-download journey through the API and separate worker. Check 51 cases, safe exit 0, distinct request/report UUIDs, and byte/SHA agreement between the validated artifact, stored report, and browser download. Give only that worker audit bootstrap variables; do not add them to the normal API process environment. Document explicit operator start/recovery and the synthetic-only limit.
- [ ] Run focused browser tests and frontend checks/build. Review the frozen whole branch once before the final gate; fix substantive findings with focused regressions. Use post-code Playbook routes selected by `scripts/checklist-router.mjs`.
- [ ] Run the final backend gate from `backend`: `uv run --frozen ruff format --check app tests`, `uv run --frozen ruff check app tests`, `uv run --frozen mypy app tests`, then `uv run --frozen pytest tests/unit tests/api tests/integration/db tests/integration/identity tests/integration/tenancy tests/integration/test_seed.py tests/integration/audit_jobs tests/test_migration_head.py tests/test_repository_contract.py -q`. This covers affected tenant/migration checks and worker lifecycle. The real worker/CLI pack runs once through the browser gate below. Require zero failures.
- [ ] On Linux, also run `uv run --frozen pytest tests/integration/audits/test_targets.py -q` once for changed model registration and application wiring. Reuse unchanged three-profile CLI evidence rather than duplicating every subprocess pack locally. Hosted CI retains the full existing gate.
- [ ] From the root check Compose; regenerate OpenAPI and the frontend schema and require no unintended drift. From `frontend` run `npm.cmd run check`, `npm.cmd run build`, and `npm.cmd run test -- --reporter=line` once. Run source secret scanning and retain counts, exits, revision, logs, and artifact hash in the verification record. Linux commands use `npm` instead of `npm.cmd`.
- [ ] Explain cost before execution: the prior full Windows backend gate took 90 minutes; this gate excludes its unchanged engine pack duplication. The new worker journey is still substantial and must finish within its 1800-second cap. Do not promise an unmeasured short runtime. No full-suite repetition for documentation or issue bookkeeping.
- [ ] Commit: `add the audit dashboard and verify its flow`.

## Handoff

Done means the role-protected portal can start, inspect, and download a real synthetic audit with recorded verification. It does not complete M4 injection/HTML work or M5 evaluation/release.

Self-review maps API/storage, worker isolation/recovery, portal cancellation, artifact integrity, and model-registration compatibility to the four tasks above. Each Review Focus item has an owning test. The worker shares records through `contracts.py`, not through privileged imports in the web service.

Recommend Native execution for these four coupled tasks, with one independent final review. Subagent-driven execution remains available but adds a fresh implementation/review context per task. Keep the repository private; future pushes or merges require the user's applicable authorization.
