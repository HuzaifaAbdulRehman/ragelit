# M4 engine-first audit design

Status: draft for review. The engine-first scope is approved; this written
spec still needs review before the implementation plan.

This spec narrows the [security design](2026-09-29-ragelit-security-design.md)
and [roadmap](../plans/2026-09-29-ragelit-roadmap.md) to the first M4 slice.
It covers M4.1 through M4.3 and the CLI/JSON portion of M4.5.

## What this slice delivers

RAGelit already provides document management, authorized chat, citations,
and private query traces. This slice adds a local command that seeds synthetic
data, exercises the bundled application, and writes a redacted audit report.

Operators and reviewers use it to reproduce access-control tests.
The project should support both an engineering portfolio and later
research evaluation, but this slice supplies control evidence, not research
novelty or a production security certification.

A complete safe run must pass every required positive and negative control.
A deliberately broken lab target must fail with the first observed exposure
stage identified. Incomplete evidence must never produce a passing gate.

Engine-first keeps one runner and report contract for the later dashboard.
Building the dashboard alongside it would add API, persistence, and UI work
before the evidence contract is settled. Starting with real-model injection
trials would make the first gate dependent on provider behavior and cost.

## Scope stays local and synthetic

Included work:

- Fixed-seed fixture generation, a checksummed manifest, and guarded seeding.
- Audit case, target, observation, scorer, and report contracts.
- Required access-control cases through the existing application boundary.
- Separate observations for retrieval, context, output, and citations.
- Safe, deliberately vulnerable, and deny-all lab profiles.
- A local CLI, redacted JSON reports, exit codes, and a deterministic CI gate.

This slice does not add audit HTTP endpoints, dashboard pages, HTML reports,
background audit jobs, production audit-result tables, prompt-injection
scoring, paid providers, or isolation-strategy benchmarks. Those remain later
M4/M5 work. A poisoned synthetic fixture is reserved for the injection pack;
its presence does not claim injection resistance.

No new runtime dependency is expected. Use the existing Pydantic, SQLAlchemy,
Qdrant client, and test tools, plus the Python standard library.

## The runner owns a separate workspace

The CLI requires explicit audit configuration. It does not silently inherit
the ordinary application's database, collection, upload directory, or model
provider from a local configuration file.

Before any seeding or lifecycle mutation, validate all of these conditions:

- The environment is local or test, never production.
- PostgreSQL and Qdrant use loopback endpoints with no redirect/proxy target.
- The database name starts with `ragelit_audit_`; the collection name does too.
- Audit storage resolves beneath its dedicated workspace directory.
- A database ownership marker binds the workspace ID, fixture version, and
  collection name to the checksummed local binding manifest.
- A new workspace has no existing application rows or vector points.
  An existing workspace must contain only its registered fixture resources.

Use an audit-only ownership record in the audit database. It is not a
production audit-result table. A name prefix alone is not proof of ownership.

Seeding refuses unknown rows, points, mismatched markers, and fixture drift.
The runner never resets an arbitrary database, truncates shared tables, or
deletes a collection merely because its name matches a prefix. Cleanup is
limited to explicitly recorded resources belonging to the verified workspace.

Bootstrap credentials may create fixtures and the application database role.
Case execution uses that non-owner role with forced PostgreSQL RLS. Bootstrap
credentials stay out of case requests and reports.

Run one audit at a time per workspace using a database lock. A second runner
fails with exit code 2 rather than sharing mutable lifecycle fixtures.

## Fixtures and their manifests

Generate three synthetic organizations, each with four groups and actors
covering owner, admin, auditor, and member roles. Use reserved example email
addresses and invented document content; no actual company or personal data.

Document fixtures cover organization-wide, group-restricted, direct-user,
ungranted, revoked, deleted, superseded, and poisoned content. Each fixture
has a stable logical ID, its expected policy, an exact canary, and a labeled
question. Organization-wide means visible within its organization, not public
on the internet.

Use a versioned generator with a fixed default seed. The template manifest
records logical actors, groups, documents, case definitions, content hashes,
and relevance labels. Its canonical serialization has a SHA-256 checksum.
A separate binding manifest records the resulting database/document/version/
chunk IDs and checksums. Runtime IDs need not match across fresh workspaces;
template identity and policy expectations must match.

Do not store passwords or access tokens in either manifest. Raw synthetic
canaries belong only in local fixture material and in-memory scoring, never
in exported audit reports.

Create uploads, grants, ingestion, replacement, and deletion through existing
application services. Ingestion must reach a verified ready state before a
case runs. Do not bypass the worker by inserting ready document rows.

A repeat run reuses unchanged base fixtures without duplicate active chunks.
Lifecycle cases have isolated fixture instances keyed by run and case so a
revocation or deletion cannot change another case. Their manifests record the
actual IDs. Interrupted instances are marked incomplete; reuse or cleanup
requires ownership verification, not an unqualified reset.

Fixture checks confirm that each forbidden probe document exists and is
retrievable by a permitted control actor before testing denial. Every retrieval
denial probe also has permitted anchor evidence, so the normal retriever does
not skip Qdrant because no eligible version exists. Fixture vectors make the
forbidden probe rank within the query limit when the lab filter is removed.
Deleted and superseded cases first record successful access, then invoke the real
lifecycle operation. This prevents an absent fixture from looking secure.

## Execute the shipped boundary

Keep the audit package under `backend/app/audits/`, with the intentionally
unsafe adapter in a separate lab module. The normal API must neither import
nor expose the unsafe adapter.

The bundled target creates an in-process application against the dedicated
local services and submits the same validated requests used by the portal.
Actors log in normally. Fresh principal and access-scope resolution must run
for every protected request, including requests made with stale tokens.
Do not construct an effective actor scope from client-supplied metadata.

The safe profile uses the existing `AuthorizedRetriever`, mandatory Qdrant
filters, context builder, citation validator, and chat service. It uses real
PostgreSQL and Qdrant with deterministic fixture embeddings and a fake citing
provider. The embedding fixture must produce reproducible query/document
matches, not a constant vector that makes controls depend on incidental order.

These embeddings test access filters and pipeline behavior. They do not
measure FastEmbed quality, realistic ranking accuracy, latency, or model safety.
Record their fixture identifier in every report.

The fake provider can echo the selected synthetic evidence and cite its chunk,
so an exposure that reaches context can be observed in output. It is never
a default for ordinary member chat and never calls an external provider.

## Contracts have distinct jobs

- `AuditCase` defines a stable case ID, actor, setup/action, required evidence,
  expected permitted/forbidden fixture IDs, and the expected response class.
- `AuditTarget` executes that action through the bundled target and returns
  observations. There is no arbitrary URL or external-target adapter.
- `AuditObservation` holds typed stage facts, sequence, completion state,
  chunk IDs, bounded canary-match metadata, HTTP status, safe decision codes,
  and a hash of the effective server-derived access scope when one exists.
- `AuditScorer` compares observations with the manifest and returns pass,
  fail, or inconclusive with fixed reason codes.
- `AuditReport` combines case results, coverage, reproducibility metadata,
  first-exposure summaries, and an overall gate decision.

Freeze the full required case inventory in the versioned pack. Reject
duplicate, missing, unknown, or mismatched results. Filtering cases for local
debugging may produce a partial report, but it must return exit code 2.

The scorer must remain independent of target assertions. The target cannot
declare itself secure, choose its expected forbidden IDs, or skip a required
positive case.

## Evidence follows the actual pipeline

Existing query traces remain private to their caller. Do not widen their
permissions to let an auditor read arbitrary member traces.

Add a trusted, in-process observer that is supplied only by the audit target.
It has no HTTP parameter and cannot change scope, filters, context, or output.
The default application path has no observer. The observer converts fixture
data into typed, bounded facts before anything is persisted.

Observe these boundaries:

| Stage | Evidence |
| --- | --- |
| Retrieval | Fused Qdrant results immediately after return, before projection validation; also the accepted chunk manifest |
| Context | Chunks actually handed to the generation provider |
| Output | Canary matches in the provider's candidate answer and separately in the delivered answer |
| Citations | Candidate citation IDs and separately the validated, delivered citation IDs |

Retrieval evidence describes results returned from Qdrant, not every internal
dense/sparse prefetch candidate. The report must state that limitation.

Record event sequence numbers. Generation happens before citation validation
in the existing service, so first exposure follows observed execution order,
not a hard-coded display order. A rejected candidate answer is not a delivered
answer. Preserve both facts without exporting either text.

For early authentication denial or no-evidence abstention, downstream stages
may be explicitly `not_reached` if a recorded terminal decision proves why.
A missing callback is `unobserved`, not empty. The case contract defines the
required stage evidence for its path. Observer failure makes the case
inconclusive; it must not disappear into a normal-looking empty observation.

Canary evidence contains only a logical canary ID, stage, match count, and
bounded character offsets where relevant. Do not include matched strings,
text snippets, filenames, questions, or prompts. Unknown or malformed stage
IDs and observations without fixture provenance make evidence incomplete.

## Required access-control pack

Each row is a required control group. Its case IDs are versioned with the pack.

| Control | Expected result |
| --- | --- |
| Organization access | An active member receives and cites the expected organization-wide evidence |
| Direct-user access | The granted user succeeds; another same-organization user cannot receive its evidence |
| Group access | A group member succeeds; a member of a different group cannot receive its evidence |
| Tenant isolation | A member cannot receive another organization's chunk or canary |
| Role/read separation | Owner/admin/auditor roles alone do not confer a document grant |
| Forged metadata | Client organization/role/group fields are rejected or ignored without changing effective access |
| Revoked membership | A previously valid token fails the next protected request after deactivation |
| Revoked grant/group membership | A previously permitted actor loses evidence after the real change succeeds |
| Deleted document | Previously permitted content is unavailable after deletion succeeds |
| Superseded version | The new version is usable; the previous version never reappears |
| Citation boundary | Candidate citations outside the context manifest are rejected and not delivered |

Negative chat controls may abstain without a 403; that is the existing product
contract. Authentication failures must have their expected stable response
class. A dependency error is not successful denial.

For positive cases, required relevant evidence must appear in retrieval,
context, delivered citations, and an answered response. For access-denial cases,
any forbidden chunk ID or canary at a reached boundary is a failure, even if
a later validation step prevents delivery. Empty downstream evidence alone
cannot prove that retrieval was safe.

The citation-boundary control deliberately supplies a registered fixture ID
outside the context as a fake provider challenge. Its expected result is an
invalid-citation error with no delivered answer or citations. That recorded
candidate is the challenge input, not a failure by itself. Unauthorized
retrieval, context, or delivered evidence still fails the case.

## Lab failures must be real and contained

The vulnerable profile uses a lab-only Qdrant adapter that omits tenant/grant
filters for the synthetic query. It executes a real query against the owned
collection; it does not fabricate observations or bypass PostgreSQL RLS.

The production retriever may reject those raw results before context is built.
The retrieval observer must still expose the fault, and the report must record
the subsequent containment. This demonstrates a detected retrieval-boundary
failure, not necessarily an end-to-end disclosure.

A separate deny-all lab profile returns no evidence. Its positive controls
must fail, proving the scorer cannot reward a broken but uniformly rejecting
system.

Unsafe and deny-all profiles require an explicit lab CLI flag plus the same
ownership guards as the safe profile. They cannot be selected through ordinary
application settings, environment variables, or web requests. No unsafe
profile is added to the web dependency graph.

## Scoring, reports, and exit codes

A case passes only when its required observations and fixture preconditions
are complete and all expected controls hold. Missing evidence, fixture drift,
unavailable services, timeouts, or unexpected runtime errors are inconclusive.
Confirmed forbidden exposure or a completed positive path that fails its
expected evidence is a failure.

A confirmed exposure remains a failure even if later stages are missing.
Coverage records any additional incomplete work. At run level, incomplete
required coverage takes exit code precedence over completed failures:

- 0: every required case passed, with complete fixture and stage evidence.
- 1: all required cases completed and at least one failed.
- 2: invalid configuration, runtime failure, missing required evidence/cases,
  or report-write failure left the run incomplete.

The CLI writes a partial redacted report when possible. Never print raw
exception strings, connection URLs, credentials, headers, or fixture bodies
as diagnostics. Use fixed error codes.

JSON reports have a versioned, allowlisted schema that forbids extra fields.
Include run ID, timestamps, profile, pack/generator/embedding/provider IDs,
Git revision and dirty flag, lockfile hashes, template/binding/configuration
hashes, per-case statuses, required coverage, stage timings, exposure sequence,
and a warning about the synthetic scope. Configuration hashing uses only
safe fields, never credentials or credential-derived hashes.

Limit stage chunk lists to the configured query limit (at most 20), match
records to 20 per stage, and diagnostic strings to fixed codes. If these
limits lose required evidence, flag truncation and return inconclusive.
Retain any confirmed failure and record the incomplete coverage separately.
Timing and generated run IDs may differ across repeat runs; case inventory,
template checksum, and decisions must remain stable.

Write atomically beneath the dedicated report directory and fail rather than
overwrite an existing run artifact. The file checksum is recorded in a sibling
receipt; a report does not embed its own checksum. No unsafe raw attachment
or provider response is allowed.

Persist these local artifacts only. Future audit database rows and dashboard
contracts will build on the reviewed report schema in a separate slice.

## Acceptance evidence before handoff

Unit tests must prove scorer behavior for positive/negative cases, deny-all,
partial inventory, duplicates, missing stages, valid early termination,
observer failure, exposure followed by containment, limits, and redaction.
Report tests must reject unsafe fields and search serialized artifacts and
captured diagnostics for synthetic canaries, passwords, tokens, prompts,
document text, and candidate answers.

Integration tests run migrations and enforce forced RLS as the application
role against real local PostgreSQL and Qdrant. They execute the entire safe
pack, then the vulnerable and deny-all profiles. Safe returns 0. The other
two return 1 with completed coverage and actionable reason codes. Missing
services or deliberately dropped observations return 2.

Workspace tests verify idempotent base seeding, isolated lifecycle cases,
concurrent-run rejection, interrupted-run handling, marker drift, unknown
resource refusal, and refusal to seed against the ordinary app target.

CLI subprocess tests check exact exit codes and parse actual report files.
Windows and Linux use the same case inventory and template checksum.
Repeated safe runs preserve their decisions without duplicate active chunks.

CI runs the safe profile as a release gate and uploads only validated redacted
reports. It separately asserts that the vulnerable and deny-all profiles
return the expected failure code, so those demonstrations cannot mask a safe
profile failure. Existing dependency and secret checks remain intact.

The handoff records observed commands and results. No claim that these
acceptance tests pass is made until the engine is implemented and verified.
