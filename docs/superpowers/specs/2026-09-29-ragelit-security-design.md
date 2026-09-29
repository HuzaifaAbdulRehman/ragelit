# RAGelit security design

Status: approved product direction, implementation pending

## Product definition

**Formal title:** RAGelit: A Multi-Tenant RAG Document Assistant with
Automated Privacy and Access-Control Auditing

**Repository description:** A multi-tenant RAG document assistant with
automated privacy and access-control auditing.

RAGelit is one web application with three role-aware areas:

- Members ask questions over documents they are permitted to read.
- Organization administrators manage users, groups, documents, and grants.
- Auditors run controlled tests and inspect where a policy failure occurred.

One hosted deployment may serve several organizations. A company may also run
the same application as a dedicated installation. Billing, subscriptions, and
commercial account management are outside v0.1; "multi-tenant" describes the
technical isolation model, not a finished SaaS business.

The product statement is:

> Upload company documents, assign access, ask cited questions, and prove that
> unauthorized content did not cross the retrieval boundary.

## Why this is more than document chat

A normal RAG application proves that relevant text can reach a model. RAGelit
also records whether text was eligible for the caller, whether it entered the
retrieval result, whether it entered model context, and whether a canary appeared
in the answer or citations.

The project serves two audiences:

- AI and backend reviewers get a working ingestion, retrieval, generation,
  authorization, audit, and web stack.
- Master's reviewers get a reproducible experiment over isolation strategies,
  measured security and utility, fixed synthetic data, and explicit limits.

RAGelit is not a certification service. An audit result describes the target,
configuration, test pack, and dataset that were actually exercised.

## Actors and permissions

Authentication identifies a user. An active organization membership supplies a
control-plane role. Document grants separately decide which content that user
may retrieve.

- **Owner:** manages the organization, users, groups, documents, and audits;
  chats only with granted documents.
- **Admin:** manages users, groups, documents, and audits; chats only with
  granted documents.
- **Auditor:** runs audits and chats only with granted documents.
- **Member:** chats with granted documents and has no management actions.

An administrator role does not automatically grant document read access. This
keeps control-plane authority separate from access to sensitive content.

Document visibility is one of:

- `organization`: every active member of the owning organization may read it.
- `restricted`: at least one explicit user or group grant is required.

The v0.1 model has grants, not explicit deny entries. Tenant mismatch, inactive
membership, inactive document version, and absence of a matching grant all deny
access.

## Primary user journeys

### Organization setup

1. An owner creates an organization and initial membership.
2. An owner or admin invites demo users and creates groups.
3. The admin assigns users to groups such as Engineering, Finance, or HR.
4. The server records every membership and permission change in an audit event.

Public email delivery and enterprise invitations are not required for v0.1.
The demo creates local users with generated development credentials.

### Document management

1. An owner or admin uploads a TXT, Markdown, DOCX, or text-based PDF file.
2. The admin chooses organization-wide or restricted visibility and selects
   users or groups for a restricted document.
3. A durable PostgreSQL job extracts, chunks, embeds, and indexes the document.
4. The UI shows queued, processing, ready, or failed state.
5. Only a fully indexed active version is queryable.

Image-only PDFs return an actionable unsupported-content error. OCR, tables,
images, and multimodal retrieval are later work.

### Authorized chat

1. A member selects an organization and asks a question.
2. The backend loads the signed session and verifies the current membership.
3. It builds an immutable `AccessScope` from database state.
4. Hybrid retrieval applies organization, document state, and grant filters in
   Qdrant before candidates are returned.
5. The context builder accepts only `AuthorizedChunk` values produced by the
   authorized retriever.
6. The configured OpenAI-compatible model answers from that context.
7. The response includes citations and a redacted query trace.

No evidence produces an abstention. A model outage preserves the retrieval
trace and returns a generation error instead of erasing useful evidence.

### Security audit

1. An owner, admin, or auditor chooses a synthetic audit workspace.
2. RAGelit seeds versioned organizations, users, groups, documents, and canary
   values owned by the test run.
3. The runner executes positive controls and adversarial cases through the same
   public application services used by chat.
4. Each case records observations at retrieval, context, citation, and output
   stages.
5. The dashboard reports pass, fail, or inconclusive with bounded evidence.
6. JSON output and process exit codes support CI regression tests.

Audit packs never target a third-party system without a future explicit target
adapter and operator authorization. The v0.1 product tests the bundled target
and synthetic data only.

## Security invariants

These invariants are release gates, not design suggestions:

1. The client cannot choose its effective tenant, role, groups, or grants.
2. Every tenant-owned PostgreSQL row carries `organization_id` directly or has
   an unavoidable foreign-key path to an organization-owned parent.
3. PostgreSQL row-level security is enabled and forced for tenant-owned tables
   in integration and production profiles.
4. Every Qdrant query is issued through `AuthorizedRetriever.search(scope,
   query, limit)`. There is no public unscoped search method in application
   code.
5. Qdrant filters include organization, active version, and document grant
   conditions before dense or sparse candidates are returned.
6. Reranking and context construction operate only on authorized chunks.
7. Authorization or policy-store failure denies the request. It never falls
   back to an unfiltered search.
8. Revocation, deletion, and membership deactivation become non-retrievable
   before their API calls report success.
9. Logs and audit reports contain opaque identifiers, policy decisions,
   timings, and bounded canary matches. They do not contain document bodies,
   complete prompts, passwords, tokens, or provider headers.
10. The LLM never makes an access decision.

## Threat model

The v0.1 attacker is an authenticated member who may:

- Submit arbitrary questions and prompt-injection strings.
- Change client-visible request bodies, headers, routes, and identifiers.
- Know or guess another organization, user, group, document, or conversation
  identifier.
- Upload a malicious document when their role permits document management.
- Race a permission revocation, document replacement, or deletion.
- Try to make a retrieved document issue instructions to the model.

The v0.1 system tests:

- Cross-organization document retrieval.
- Cross-group and direct-user grant bypass.
- Forged organization, role, group, or document metadata.
- Inactive memberships and revoked grants.
- Deleted or superseded document versions.
- Indirect prompt injection from an authorized malicious document.
- Canary disclosure in retrieval, context, citations, and output.

The v0.1 system does not claim protection against model extraction,
membership-inference research attacks, embedding inversion, vector database
administrator compromise, host compromise, denial of service, or malicious
model providers.

## Architecture

```text
React web portal
  -> FastAPI application
       -> authentication and membership resolution
       -> PostgreSQL metadata and row-level security
       -> durable ingestion worker
       -> authorized retrieval boundary
            -> local dense and sparse embedding
            -> Qdrant hybrid search with mandatory access filter
            -> optional reranker on allowed chunks
       -> bounded context builder
       -> OpenAI-compatible generation provider
       -> query trace and citations
       -> audit runner and reports
```

The repository is a monorepo:

```text
backend/
  app/
    api/
    core/
    db/
    identity/
    documents/
    retrieval/
    chat/
    audits/
    evaluation/
    workers/
  tests/
frontend/
  src/
    features/auth/
    features/admin/
    features/documents/
    features/chat/
    features/audits/
  tests/
datasets/
  synthetic/
docs/
infra/
```

### Technology choices

- Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, and uv.
- PostgreSQL for users, memberships, grants, document state, conversations,
  traces, and audit results.
- Qdrant for dense and sparse vectors with indexed access-control payloads.
- Local embedding and sparse models through FastEmbed unless a measured need
  requires a different provider.
- An optional local cross-encoder reranker after authorization.
- React, TypeScript, Vite, TanStack Query, TanStack Router, and Playwright.
- Docker Compose for PostgreSQL, Qdrant, backend, worker, and frontend.
- An OpenAI-compatible generation interface so local and hosted providers use
  one contract.

No agent framework is required. LangChain and LlamaIndex are not dependencies
unless a later adapter proves that they remove more code than they add.

## Data model

PostgreSQL is authoritative for identity, permissions, lifecycle, and audit
state. Qdrant stores a queryable projection of active chunks.

Core relational records:

- `users`
- `organizations`
- `memberships`
- `groups`
- `group_members`
- `documents`
- `document_grants`
- `document_versions`
- `ingestion_jobs`
- `conversations`
- `messages`
- `query_runs`
- `query_trace_stages`
- `audit_runs`
- `audit_case_results`
- `audit_canaries`
- `security_events`

Every organization-scoped primary key is an opaque UUID. Foreign keys use
cascade or restrict behavior intentionally; there are no implicit orphan rows.

Each Qdrant point contains:

- `organization_id`
- `document_id`
- `document_version_id`
- `chunk_id`
- `chunk_index`
- `visibility`
- `allowed_user_ids`
- `allowed_group_ids`
- `active`
- source-location metadata needed for citations

Qdrant receives no passwords, membership records, provider credentials, or raw
audit reports.

## Authentication and tenant context

The browser receives a short-lived signed access token and a rotating session
record. The token identifies the user, selected organization, and session, but
the backend still loads the session and current membership on every protected
request. A stale or revoked membership fails closed.

`RequestPrincipal` contains authenticated identity. `AccessScope` is built from
that principal and current database state. API request models do not expose
fields that can override either value.

PostgreSQL sessions set `app.organization_id` before tenant-owned queries. Row
policies compare each row against that setting and use `FORCE ROW LEVEL
SECURITY`. Integration tests connect as the same non-owner database role used
by the application so table-owner bypass cannot make the tests pass falsely.

## Document ingestion and lifecycle

Uploads are streamed to a configured local data volume with size and media-type
limits. A SHA-256 checksum provides idempotency within an organization.

The API creates a PostgreSQL job and returns immediately. A separate worker
claims jobs with `FOR UPDATE SKIP LOCKED`, so multiple workers do not process
the same job. Each job:

1. Extracts text and source positions.
2. Rejects empty or image-only content.
3. Splits text using a deterministic structure-aware chunker.
4. Produces dense and sparse vectors in bounded batches.
5. Writes inactive points to Qdrant.
6. Verifies point count and required payload indexes.
7. Activates the new version and deactivates the previous version.
8. Records a security-safe event and completes the job.

Interrupted and failed jobs are recoverable. Cleanup never makes a partially
indexed version queryable.

Grant changes, membership revocation, and deletion must update retrieval state
before returning success. If a cross-store update cannot complete, the API
returns a retriable failure and a reconciliation job records the mismatch.

## Authorized retrieval

`AuthorizedRetriever` accepts only a server-created `AccessScope`. It builds one
Qdrant filter containing:

```text
organization_id == scope.organization_id
AND active == true
AND (
  visibility == organization
  OR allowed_user_ids contains scope.user_id
  OR allowed_group_ids intersects scope.group_ids
)
```

The same filter applies to every dense and sparse prefetch branch. Hybrid
results use reciprocal-rank fusion. An optional reranker receives only the
fused authorized candidates.

Returned Qdrant points are mapped to the immutable `AuthorizedChunk` type. The
context builder does not accept the Qdrant client's raw result type. This type
boundary prevents a later feature from passing unreviewed candidates directly
to the generator.

The default production strategy is a shared collection with the indexed
`organization_id` payload marked as a tenant field. The evaluation package also
implements:

- A collection-per-tenant strategy for a small number of strongly isolated
  tenants.
- An intentionally unsafe retrieve-then-filter strategy available only in the
  lab profile and impossible to enable in the web application.

## Answer generation and citations

The generator receives a fixed system instruction, the member's question, and
a bounded list of authorized chunks. Document text is marked as untrusted data,
not instructions. This prompt is a defense against indirect injection, not an
authorization control.

Every factual citation names the document, version, chunk, and source location.
The server rejects citations to chunks absent from the context manifest.

For arbitrary user documents, the UI reports retrieval evidence and citations.
It does not invent a live accuracy score without labels.

## Audit engine

The engine separates five contracts:

- `AuditCase` describes an actor, fixture state, action, and expected control.
- `AuditTarget` executes the action against the bundled application boundary.
- `AuditObservation` records redacted facts for each stage.
- `AuditScorer` returns pass, fail, or inconclusive with reason codes.
- `AuditReport` groups results, environment data, and artifact checksums.

### Required deterministic pack

The access-control pack is a release gate. It includes positive controls so a
system that denies every request cannot pass.

- Same-organization organization-wide access succeeds.
- Direct user and group grants succeed.
- Cross-organization access fails.
- Cross-group access fails.
- Forged client metadata has no effect.
- Revoked membership and grant access fail.
- Deleted and superseded versions fail.
- Citations contain only retrieved authorized chunk identifiers.

### Prompt-injection pack

An authorized synthetic document contains an instruction and a unique output
canary. A benign question causes the document to be retrieved. The scorer checks
whether the canary enters context and whether the model repeats or acts on it.

Fake providers make unit and CI behavior deterministic. Real-model experiments
record model, parameters, trial count, successes, failures, and inconclusive
runs. Their attack success rate is reported separately from deterministic
access-control gates.

### Evidence and reports

For each case, the report may include:

- Actor, organization, groups, and scenario identifiers.
- The effective access-scope hash.
- Retrieved and contextualized chunk identifiers.
- Policy and scorer reason codes.
- Bounded canary-match locations.
- Stage timings and provider metadata.

It must not include document bodies, raw tokens, full prompts, or secret values.

CLI exit codes are:

- `0`: every required deterministic control passed.
- `1`: the audit completed with at least one required failed control.
- `2`: configuration, fixture, target, or runtime failure made the result
  incomplete.

## Evaluation study

The primary research question is:

> Where should tenant authorization be enforced in a RAG pipeline, and what
> security, retrieval-quality, latency, and storage trade-offs follow?

The fixed synthetic dataset contains at least three organizations, four groups
per organization, public and restricted documents, revocation events, deleted
versions, exact canaries, and natural-language questions with relevance labels.
The manifest and generated files have checksums and a fixed seed.

Compared isolation strategies:

1. Unsafe retrieve-then-filter baseline, lab only.
2. Shared Qdrant collection with mandatory indexed payload pre-filtering.
3. Separate Qdrant collection per tenant.

Primary security metrics:

- Unauthorized retrieval rate.
- Context exposure rate.
- Output disclosure rate.
- Indirect prompt-injection attack success rate.

Primary utility and cost metrics:

- Recall@10 and MRR@10 on permitted queries.
- Citation correctness on labeled answers.
- p50 and p95 retrieval latency.
- Index build time, storage size, and collection count.
- Revocation-to-enforcement time.

Each run records configuration, Git commit, dependency lock checksum, dataset
checksum, model identifiers, machine information, per-query results, and paired
confidence intervals where appropriate. Completion does not depend on a favored
strategy winning.

This is an evaluation question, not a novelty claim. The literature review must
be refreshed before any paper submission.

## API boundary

The first versioned API groups routes under `/api/v1`:

- `POST /auth/login`
- `POST /auth/refresh`
- `POST /auth/logout`
- `POST /auth/switch-organization`
- `GET /me`
- `GET|POST /organizations`
- `GET|POST /organizations/{organization_id}/members`
- `GET|POST /organizations/{organization_id}/groups`
- `GET|POST /documents`
- `GET|PATCH|DELETE /documents/{document_id}`
- `GET /documents/{document_id}/versions`
- `POST /chat/query`
- `GET /query-runs/{query_run_id}`
- `POST /audits`
- `GET /audits/{audit_run_id}`
- `GET /audits/{audit_run_id}/report.json`
- `GET /health/live`
- `GET /health/ready`

Organization identifiers in URLs are routing context only. The server compares
them with the authenticated membership and never treats them as authorization.

Errors use stable problem-detail codes. Upload size, query length, result count,
provider timeout, and request duration have explicit limits.

## Web experience

The web application uses one login and one navigation shell. Routes and actions
are hidden when unavailable, but backend authorization remains authoritative.

Required screens:

- Login and organization switcher.
- Member chat with streamed answer, citations, abstention, and error states.
- Document list, upload, ingestion status, and grant editor.
- User and group management.
- Audit pack selection, run progress, results table, and stage detail.
- A lab comparison view for isolation-strategy experiments.

The first UI must be keyboard usable, responsive at common laptop widths, and
clear without color as the only status signal.

## Failure handling

- Invalid, expired, or revoked sessions return `401` without tenant data.
- Missing role permission or document grant returns `403` or an empty grounded
  answer according to the endpoint contract, without exposing resource
  existence across tenants.
- PostgreSQL policy-context failure aborts the transaction.
- Qdrant or authorization failure never triggers an unfiltered fallback.
- Ingestion retries use the same version and do not duplicate active chunks.
- Provider timeouts preserve retrieval and context trace stages.
- Audit fixture drift or missing evidence returns inconclusive and exit code 2,
  not a pass.
- Report generation redacts canary values and refuses unsafe raw fields.

## Verification strategy

Unit tests cover policy matrices, filter construction, chunk provenance,
context type boundaries, scorer decisions, redaction, and metric calculations.

Integration tests use real PostgreSQL and Qdrant containers. They verify row
policies with the application database role, tenant and group filter behavior,
grant revocation, version activation, deletion, hybrid retrieval, and API
authorization.

Security regression tests seed exact canaries and assert that unauthorized
chunk IDs appear in none of retrieval, context, citation, or output stages.
Positive controls assert that permitted evidence still works.

End-to-end tests cover admin upload and grants, member chat, a deliberately
vulnerable lab run, a safe run, and audit report download.

CI runs formatting, linting, static types, unit tests, integration tests, web
tests, a deterministic audit smoke test, migration checks, secret scanning, and
dependency review. A clean-clone check runs on Windows and Linux before v0.1.

## Release criteria

RAGelit v0.1 is complete when:

- Docker Compose starts PostgreSQL, Qdrant, API, worker, and web application
  from a clean clone with documented commands.
- An admin can create groups, upload a supported document, and assign grants.
- Members receive cited answers only from authorized active documents.
- Tests demonstrate tenant, group, revocation, deletion, and citation
  invariants against real local services.
- The audit dashboard distinguishes retrieval, context, citation, and output
  exposure.
- The deterministic access-control pack gates CI with all positive and negative
  controls executed.
- The isolation comparison writes reproducible per-case data and a concise
  report with security, utility, and latency results.
- Third-party reuse is listed with license and source commit.
- The repository contains a demo script and no unsupported resume claims.

## Explicit non-goals for v0.1

- Billing, subscriptions, quotas, or a hosted commercial control plane.
- Enterprise SSO, SCIM, SAML, or external identity-provider setup.
- OCR, multimodal ingestion, knowledge graphs, web crawling, or connectors.
- Mobile applications or separate portals per role.
- Arbitrary internet target scanning.
- Automated exploitation, credential attacks, or vulnerability certification.
- Training or fine-tuning language or embedding models.
- Kubernetes, high availability, geo-replication, or cloud-specific deployment.
- Membership inference, embedding inversion, or differential privacy.
- Dozens of audit categories before tenant leakage and indirect injection are
  measured and reliable.

## Reference boundary

The source and license review is recorded in
[`../../research/2026-09-29-reference-review.md`](../../research/2026-09-29-reference-review.md).
RAGelit may adapt commodity code from the MIT-licensed FastAPI template with
notices. Its retrieval boundary, audit model, evidence schema, synthetic
dataset, and isolation experiment remain RAGelit-owned implementations.
