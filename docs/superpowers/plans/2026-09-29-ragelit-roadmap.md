# RAGelit Delivery Roadmap

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement each approved phase plan
> task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a multi-tenant RAG document assistant that enforces document
access during retrieval and produces reproducible evidence for tenant leakage
and indirect prompt-injection tests.

**Architecture:** A React portal calls a FastAPI application backed by
PostgreSQL and Qdrant. Server-derived identity produces an immutable access
scope, every vector query carries that scope, and a separate audit package
observes retrieval, context, citations, and output without storing document
bodies in reports.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL,
Qdrant, FastEmbed, React, TypeScript, Vite, TanStack Query, Playwright, pytest,
Docker Compose, GitHub Actions

**Spec:** `docs/superpowers/specs/2026-09-29-ragelit-security-design.md`

## Global Constraints

- The web application has one portal with role-aware admin, member, and audit
  areas.
- The backend derives organization, role, groups, and grants from the signed
  session and current database state.
- Authorization happens inside Qdrant search; no production path may retrieve
  broadly and filter afterward.
- PostgreSQL row-level security is enabled and forced for tenant-owned tables.
- Administrators do not gain document read access unless a document policy
  grants it.
- The LLM never decides whether a document may be read.
- No Qdrant or policy-store failure may cause an unfiltered fallback.
- Audit fixtures are synthetic and owned by the local RAGelit deployment.
- Reports and logs do not contain document bodies, full prompts, access tokens,
  passwords, provider headers, or unredacted canary values.
- RAGelit does not claim security certification or research novelty.
- The ignored `references/` directory is never committed.
- Copied or substantially adapted third-party code is recorded in
  `THIRD_PARTY_NOTICES.md` before its first commit.
- Every implementation issue ends with an observable behavior, a test, or a
  reproducible artifact.

## Review Focus

- A valid user changes the organization identifier in a URL or request body:
  protected handlers must return no data from the other organization. Covered
  by M1 tenant-route integration tests.
- A membership or group grant is revoked while the user's token remains valid:
  the next protected request must use current database state. Covered by M1
  session tests and M2 revocation tests.
- Qdrant is unavailable or rejects an access filter: chat must fail closed and
  preserve a safe error trace. Covered by M2 retrieval failure tests.
- An audit case cannot prove whether a canary reached a stage: the case must be
  inconclusive, never pass. Covered by M4 scorer and report tests.
- A member has permission to read a document but retrieval finds no evidence:
  the assistant must abstain without describing the document as forbidden.
  Covered by M2 chat contract tests.

## Milestone dependency map

```text
M0 Product Definition
  -> M1 Secure Foundation
       -> M2 Authorized RAG
            -> M3 Product Experience
            -> M4 Security Audit Engine
                 -> M5 Evaluation and v0.1 Release
```

M3 and M4 may proceed in parallel only after M2 API contracts are frozen. No
later milestone may bypass an earlier security invariant to unblock a demo.

## M0 - Product Definition

Outcome: reviewers can tell what RAGelit builds, what it tests, how it differs
from existing projects, and which claims are out of scope.

### Task M0.1: Approve the replacement design

- [ ] Review the product definition, roles, access model, threat model, API,
  audit packs, evaluation study, and non-goals.
- [ ] Resolve any requested changes in the design document.
- [ ] Mark the former adaptive-retrieval design as superseded.
- [ ] Commit the approved design as one documentation change.

### Task M0.2: Freeze the reference and license boundary

- [ ] Verify each local reference clone against its recorded commit and
  license.
- [ ] Create `THIRD_PARTY_NOTICES.md` with the FastAPI template attribution
  before adapting its files.
- [ ] Record which files are copied, substantially adapted, or only studied.
- [ ] Add a release check that rejects a tracked `references/` path.

### Task M0.3: Build the control matrix

- [ ] Map every threat-model case to its enforcement point and required test.
- [ ] Assign each control a stable identifier such as `TENANT-001` or
  `INJECT-001`.
- [ ] Mark deterministic controls as release gates and model-dependent controls
  as measured observations.
- [ ] Publish the matrix under `docs/security/control-matrix.md`.

Gate: the design and control matrix have no unresolved product decisions.

## M1 - Secure Foundation

Outcome: a clean clone starts a tested API and web shell; login creates a
server-verified principal; organization and role boundaries hold in PostgreSQL.

Detailed plan:
`docs/superpowers/plans/2026-09-29-secure-foundation.md`

### Planned issues

1. Adapt the FastAPI and React project skeleton with attribution.
2. Add configuration, health checks, and local Compose services.
3. Create organization, user, session, membership, group, and group-member
   models and migrations.
4. Implement password login, refresh rotation, logout, and session revocation.
5. Implement the control-plane RBAC action matrix.
6. Build `RequestPrincipal` and immutable `AccessScope` from current database
   state.
7. Enforce PostgreSQL tenant context and row-level security.
8. Add organization, member, and group APIs with negative tenant tests.
9. Seed two demo organizations and role-specific users.
10. Run backend, frontend, migration, secret, and container smoke checks in CI.

Gate: one organization's credentials cannot read or mutate the other
organization's rows through any shipped API or direct application-role query.

## M2 - Authorized RAG

Outcome: permitted members can upload and query documents; unauthorized chunks
cannot leave Qdrant or reach model context.

### Task group M2.1: Durable document ingestion

- [ ] Define document, grant, version, and job schemas with lifecycle states.
- [ ] Stream uploads with media-type and size limits.
- [ ] Extract TXT, Markdown, DOCX, and text-based PDF content.
- [ ] Implement deterministic structure-aware chunking with source locations.
- [ ] Claim PostgreSQL jobs with `FOR UPDATE SKIP LOCKED` in a separate worker.
- [ ] Make retries idempotent by organization and SHA-256 checksum.
- [ ] Reject empty and image-only documents with stable problem codes.

### Task group M2.2: Qdrant projection and authorized search

- [ ] Create dense, sparse, tenant, grant, document, version, and active payload
  indexes.
- [ ] Implement `AccessScope`, `AuthorizedChunk`, and `AuthorizedRetriever`
  contracts.
- [ ] Build one mandatory compound Qdrant filter from the server scope.
- [ ] Apply that filter to dense and sparse hybrid branches.
- [ ] Fuse authorized ranks and rerank only authorized candidates.
- [ ] Remove or deactivate stale points before lifecycle changes report success.
- [ ] Make Qdrant and policy failures fail closed.

### Task group M2.3: Grounded generation

- [ ] Define an OpenAI-compatible provider contract and deterministic fake.
- [ ] Build bounded context from `AuthorizedChunk` values only.
- [ ] Validate citations against the context manifest.
- [ ] Abstain when no authorized evidence clears the configured threshold.
- [ ] Persist safe retrieval, context, citation, generation, and error stages.
- [ ] Keep provider errors separate from retrieval results.

### Task group M2.4: Security regression suite

- [ ] Test organization-wide, direct-user, and group-granted positive controls.
- [ ] Test cross-organization and cross-group negative controls.
- [ ] Test forged client fields, inactive memberships, and stale tokens.
- [ ] Test grant revocation, deletion, replacement, and interrupted ingestion.
- [ ] Assert unauthorized identifiers appear in no context or citation manifest.

Gate: all deterministic authorization tests pass against real PostgreSQL and
Qdrant containers, including revocation and deletion.

## M3 - Product Experience

Outcome: an owner can manage access, a member can chat, and an auditor can read
evidence without using API documentation or database tools.

### Task group M3.1: Application shell

- [ ] Implement login, logout, organization switching, and route guards.
- [ ] Render role-appropriate navigation without treating it as authorization.
- [ ] Add consistent loading, empty, denied, unavailable, and retry states.
- [ ] Meet keyboard, focus, label, contrast, and responsive-layout checks.

### Task group M3.2: Administration

- [ ] Build member and group management.
- [ ] Build document upload, state, version, delete, and retry flows.
- [ ] Build an explicit organization or restricted grant editor.
- [ ] Explain that admin role alone does not grant document read access.

### Task group M3.3: Member chat

- [ ] Stream answer state and show cited document locations.
- [ ] Show abstention without implying a forbidden document exists.
- [ ] Show a redacted trace with strategy, allowed chunk identifiers, and
  timings.
- [ ] Preserve the trace when generation fails.

Gate: Playwright completes owner setup, group grant, upload, member chat,
cross-group denial, and document revocation journeys.

## M4 - Security Audit Engine

Outcome: a controlled audit run produces deterministic, stage-decomposed
evidence and a CI decision.

### Task group M4.1: Synthetic audit organization

- [ ] Generate at least three organizations and four groups per organization
  from a fixed seed.
- [ ] Create public, group-restricted, user-restricted, revoked, deleted, and
  poisoned document fixtures.
- [ ] Assign exact canaries and relevance labels without using real personal or
  company information.
- [ ] Write a checksummed manifest and make seeding idempotent.

### Task group M4.2: Audit contracts and runner

- [ ] Implement `AuditCase`, `AuditTarget`, `AuditObservation`, `AuditScorer`,
  and `AuditReport` contracts.
- [ ] Execute cases through the same application services used by chat.
- [ ] Record retrieval, context, citation, and output observations separately.
- [ ] Mark missing required evidence inconclusive.
- [ ] Persist run configuration and artifact checksums.

### Task group M4.3: Access-control pack

- [ ] Add positive controls for organization, direct-user, and group grants.
- [ ] Add negative controls for tenant, group, metadata spoof, revocation,
  deletion, and superseded versions.
- [ ] Fail the gate if required positive or negative controls do not execute.
- [ ] Prove that a deny-all target cannot pass.

### Task group M4.4: Indirect prompt-injection pack

- [ ] Seed authorized documents with versioned injection payloads and unique
  output canaries.
- [ ] Use deterministic fake providers for CI.
- [ ] Record trial count and attack success rate for real providers.
- [ ] Keep model-dependent results separate from deterministic access controls.

### Task group M4.5: Reports and CI

- [ ] Render redacted JSON and HTML reports.
- [ ] Implement exit codes 0, 1, and 2 from the specification.
- [ ] Add audit list, progress, summary, and stage-detail screens.
- [ ] Upload the deterministic report as a CI artifact.

Gate: the safe profile passes, the deliberately vulnerable lab profile fails,
and both results identify the exact first exposed stage.

## M5 - Evaluation and v0.1 Release

Outcome: the repository contains reproducible measurements, a clean-clone demo,
and only claims supported by committed evidence.

### Task group M5.1: Isolation strategies

- [x] Implement shared-collection payload pre-filtering as the production
  strategy.
- [x] Implement collection-per-tenant isolation behind the same store contract.
- [x] Implement retrieve-then-filter only inside the lab package with a runtime
  guard that rejects production configuration.
- [x] Run the same functional and audit contract suite against each strategy.

The 7 October local checkpoint records the three-strategy functional run and
validated report set. The observations use deterministic fixtures and do not
replace the real-model benchmark.

### Task group M5.2: Reproducible benchmark

- [x] Pin the synthetic dataset, embedding model, sparse model, reranker, and
  generation settings.
- [ ] Measure unauthorized retrieval, context exposure, output disclosure,
  injection success, Recall@10, MRR@10, citation correctness, p50/p95 latency,
  index time, storage, and revocation delay.
- [ ] Write per-query machine-readable artifacts and paired confidence
  intervals where appropriate.
- [ ] Record failures and negative results without changing the preregistered
  primary metrics.

### Task group M5.3: Hardening and release evidence

- [x] Run dependency, secret, static, and container scans.
- [x] Test malformed uploads, oversized requests, concurrency, restart recovery,
  and provider timeouts.
- [x] Run setup and the demo from a clean Windows clone and a Linux CI runner.
- [x] Verify tracked files, license notices, migrations, example configuration,
  and absence of secrets.

The [9 October file review](../../verification/2026-10-07-local-release-checkpoint.md#release-file-review-9-october)
records the reference commits, preserved license text, unchanged migration and
configuration baseline, and clean redacted branch-history scan.

The [9 October coverage audit](../../verification/2026-10-07-local-release-checkpoint.md#hardening-coverage-audit-9-october)
maps existing malformed-upload, size-limit, concurrency and timeout assertions
to the passing main revision. The focused Windows restart-and-recovery check
then passed 10 tests against a disposable database. Audit execution was stubbed
and lease expiry accelerated; the checkpoint records those limits. This closes
the combined hardening checkbox, not the separate release or benchmark gates.

### Task group M5.4: Release documentation

- [x] Write a short README with architecture, setup, demo, evidence, limits,
  attribution, and screenshots.
- [x] Publish the threat model, control matrix, API schema, benchmark method,
  and raw result format.
- [x] Record a demo showing a vulnerable run, first exposed stage, control fix,
  and passing rerun.
- [x] Draft resume and research wording using only reproduced measurements.
- [x] Record the user's explicit repository visibility decision.

The user made the repository public and asked to keep that visibility. This
replaces the original private-delivery requirement; no visibility change was
made by the implementation workflow.

The 8 October checkpoint links the passing full Linux CI run and Windows replay
of its audit artifacts. These are deterministic setup/demo results, not a
completed real-model benchmark.

The dependency and secret scans are clean. Docker Scout ran against both pinned
service images and found unresolved critical/high findings; remediation remains
open even though the scan itself is complete.

The [9 October candidate checks](../../verification/2026-10-07-local-release-checkpoint.md#remaining-compatibility-checks)
record 252 DB/API/migration passes across three runs against the local PostgreSQL
zlib candidate. Four model-dependent cases remain skipped; the three image
opt-in checks passed separately. This does not close container disposition or
the real-model benchmark gate. Those checks left Compose and CI unchanged.

The later [fresh-install promotion](../../verification/2026-10-07-local-release-checkpoint.md#fresh-install-patch-promotion-9-october)
selects both tested library-patch recipes in Compose and CI.
[PR #28 is merged](https://github.com/HuzaifaAbdulRehman/ragelit/pull/28);
its full hosted CI passed 794 unit tests, 260 integration tests with four skips,
and 57 browser journeys. All validated audit export sets uploaded.
The [merge checkpoint](../../verification/2026-10-07-local-release-checkpoint.md#merged-patch-defaults-and-hosted-ci-9-october)
records the source, run links and remaining gates. Existing data has not been
migrated. Container disposition and real-model measurements remain open.

Gate: v0.1 starts from a clean clone, runs its audit without private services,
and reproduces every number used in public prose.

## Work-item rules

When this roadmap becomes GitHub issues:

- One issue represents one independently reviewable behavior or artifact.
- Every issue names its milestone, dependencies, acceptance criteria, test
  commands, security notes, and excluded work.
- P0 means required for the current milestone. P1 means required before v0.1.
  P2 means useful only after the core path is stable.
- An issue closes only after verification output is recorded in the pull
  request or commit handoff.
- New feature ideas enter M5 or a post-v0.1 milestone unless they block a
  written release criterion.
- No implementation starts until M0 documents are reviewed and approved.
