# M4 audit dashboard design

Status: design and task plan approved on 4 October 2026. Implementation and
verification completed on 5 October. The real journey passed on Windows
and Linux; see the [verification record](../../verification/2026-10-04-audit-dashboard.md).

This extends the [engine-first design](2026-10-02-audit-engine-design.md).
The engine is committed at `772b6d1`; its
[verification record](../../verification/2026-10-04-audit-engine.md)
remains evidence for that revision, not for new dashboard code.

## Outcome and limits

An owner, admin, or auditor can start a synthetic access-control audit from
the existing portal, see its job status, inspect saved case/stage results,
and download redacted JSON. Members cannot use these endpoints or screens.
The user chose this flow over a report viewer that still needs manual CLI runs.

The first dashboard runs only the complete 51-case safe profile. It never
tests uploaded company documents or certifies an organization as secure.
Broken lab profiles remain CLI-only. Injection trials, HTML exports,
real-model quality, benchmarks, billing, and streaming are outside this slice.
Use the existing FastAPI, SQLAlchemy, PostgreSQL, React, and query tools.
No new runtime dependency or queue service is planned.

## API and tenant access

Add these routes under the existing `/api/v1` prefix:

- `POST /audits`: accept an empty, extra-forbidden command; return 202 and
  a queued run summary. No client profile, organization, target URL, path,
  credentials, case selection, or shell argument is accepted.
- `GET /audits`: return newest-first summaries with the existing bounded
  limit/offset pagination convention.
- `GET /audits/{id}`: return job status and validated case/stage results
  when a report exists.
- `GET /audits/{id}/report.json`: download the validated report bytes.
  An authorized run without a report returns a fixed not-ready error.

Use the current principal and `Action.AUDITS_RUN` on every request,
including downloads. Organization and requester come from server-resolved
identity. Forced RLS and organization-scoped lookups cover the new table;
another organization's ID returns 404. A member is denied even for its own
organization. Revocation and role changes take effect on the next request.
Audits do not widen access to member query traces or source documents.

Allow only one outstanding run per organization, enforced in the database
under concurrent requests. A duplicate start returns 409 with a fixed code.
The initial deployment uses one audit worker for one explicitly configured
organization at a time. A deployment-wide advisory lock prevents two healthy
audit workers from executing jobs concurrently against the local services.
Queued jobs remain visible if the operator has not started that worker.

## Storage and job states

Add one `audit_runs` table through the next Alembic migration. It contains
an opaque request ID, organization/requester IDs, timestamps, job state,
claim token, lease deadline, fixed error code, outcome/exit code, and optional
validated report content, report ID, and SHA-256. Foreign keys preserve
organization consistency. A partial unique index protects outstanding jobs.

Keep request ID and the engine-generated report run ID separate; do not
change the established engine ID contract to make them match. Store the
original validated UTF-8 report and checksum, bounded to the existing
8 MiB artifact limit, so a download preserves the checked bytes. Never store
bootstrap passwords, URLs, tokens, raw diagnostics, prompts, or document bodies.
Persist terminal status and any report together only for the current running
claim with an unexpired lease. A stale claimant cannot overwrite recovery.

States are queued, running, finished, and recovery_required. Queued/running
outcomes are unknown. A finished report maps engine exit 0 to pass, 1 to fail,
and 2 to inconclusive. Service errors, corrupt artifacts, and interruption
without a usable report also yield inconclusive with a fixed code. Completed
failures in a valid partial report remain visible; missing coverage never passes.

## Separate worker and owned fixtures

The worker follows the existing organization-scoped ingestion-worker pattern.
It claims a queued row transactionally, rechecks that the requester is active
and still allowed to run audits, then releases the transaction before execution.
If that permission disappeared, finish inconclusively without running fixtures.

Only this worker receives explicit audit bootstrap configuration. Derive a fresh
`ragelit_audit_` database/collection name from the request UUID and a matching
directory beneath its configured audit root. Retain the engine's loopback,
ownership, forced-RLS, path, and effective libpq destination guards. Neither
the normal application database nor upload storage is an audit target.

Launch `python -m app.audits.cli` with a fixed argument list, no shell, no
`--lab`, and no case filter. Give the child only required runtime variables
and explicit audit configuration, not the ordinary application's provider
or authentication secrets. Poll the child while renewing the job lease.
Use the existing 1800-second CLI test deadline as the initial execution cap.

Before accepting a result, require a bounded diagnostic, a canonical report
ID, a matching report/receipt in that job's owned workspace, and the existing
single-report validator. Require safe profile and agreement between process
exit, diagnostic exit, and validated report exit. Do not use the three-profile
release-directory validator for one safe job. The web dependency graph may
use report contracts but must not import the runner, workspace, or lab adapter.

On timeout or graceful shutdown, stop and reap the owned child before releasing
its claim. An expired lease or unconfirmed child shutdown needs recovery:
show inconclusive, block new execution for that organization, and do not
replay the job automatically.
An operator-only recovery command resolves the exact run after confirmation
that its worker/child stopped; it retains fixtures and reports, and cannot
convert the run to pass. No automatic deletion, reset, or orphan adoption.
Abrupt laptop/worker failure therefore needs an explicit recovery step.

## Portal

Replace the disabled Audit navigation item for owner/admin/auditor roles.
Add an audit list/start page and a run-detail page using the existing shell,
workspace-scoped queries, generated API types, and component styles.

The list shows start time, status, outcome, and report availability. The detail
page shows coverage, fixed case reasons, first exposure, and the recorded stage
sequence, preserving observed versus not-reached versus unobserved evidence.
Keep candidate and delivered output/citation observations distinct. Display
the report's synthetic-scope and fused-retrieval limitations.

Poll queued/running runs every two seconds while visible. Show no invented
percentage or per-case live progress. Stop polling on terminal/recovery state,
unmount, logout, role loss, or tenant switch. Existing workspace cancellation
and cache isolation must prevent delayed results from reaching another tenant.
Authentication or permission denial stops polling and clears the audit view.
Render report values as text, not HTML. A reportless error shows an explanation
and recovery guidance, not empty results that look like a passing audit.

## Acceptance and verification budget

Use focused RED/GREEN tests during implementation. Verify role access, forced
RLS as the non-owner application role, cross-tenant IDs, concurrent admission,
requester revocation, claim ownership, interrupted recovery, timeout cleanup,
artifact integrity/redaction, and exact 0/1/2 outcome mapping.

Browser checks cover role-aware navigation, start/poll/detail/download,
inconclusive errors, and tenant-switch cancellation. One real worker/CLI
journey must prove persisted results agree with the validated engine artifact.
Mocks cover scheduling/UI edge cases, not that end-to-end claim.

The new migration/model registry must remain compatible with the audit
workspace's exact table snapshots and existing ingestion/chat flows. Check
the web dependency graph and generated API drift. Do not retest unchanged
engine behavior solely for bookkeeping, but rerun affected regressions when
shared application/migration code changes. Define one final slice gate in the
implementation plan and record commands, source revision, results, and logs.
The prior Windows backend gate took 90 minutes; explain measured costs before
any long run. Documentation changes alone do not trigger that suite.

Done requires the working portal flow, recorded verification, and meaningful
local implementation commits. This spec is not proof of implemented behavior.
No push, merge, visibility change, paid provider, or cloud job is included.
