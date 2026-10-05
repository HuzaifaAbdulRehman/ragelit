# RAGelit

RAGelit is a multi-tenant document assistant in active development. Owners and
admins can manage groups, upload documents, and edit reading access in one web
portal. Members can ask questions over permitted evidence and inspect cited
answers or their own query traces.

Owners, admins, and auditors can queue a synthetic access-control audit and
inspect its saved results in Audits. The separate operator worker runs invented
fixtures only. Saved reports download as original JSON or a self-contained HTML
view. A separate CLI tests instructions hidden in synthetic documents. Retrieval
benchmarks and live streaming remain planned. This is a development build, not
a production deployment.

See the [security control matrix](docs/security/control-matrix.md) for enforcement
code, regression coverage and the release checks still pending.

## Run locally

Install Docker Desktop with Compose, Python 3.12 or 3.13,
[uv](https://docs.astral.sh/uv/), and Node.js 22 with npm.
Copy `.env.example` to `.env` in the repository root. The example credentials
are for local development only.

```powershell
Copy-Item .env.example .env
docker compose up -d postgres qdrant
```

On macOS or Linux, use `cp .env.example .env` instead. Prepare and start the API:

```console
cd backend
uv sync --frozen
uv run alembic upgrade head
uv run python -m app.seed
uv run uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port 8000
```

The seed command prints random passwords for newly created demo users. It does
not print or reset them on later runs. Keep that first output for demo login.
In another terminal, start the web app:

```console
cd frontend
npm ci
npm run dev
```

Open <http://localhost:5173/login>. API documentation is at
<http://localhost:8000/docs>.

## Use the portal

Sign in as `owner@northstar.example` with the password printed by the seed.
People changes existing members' roles and activation; it does not send
invitations. In Groups, create a team and add an existing member.

Open Documents and upload a supported file. It starts restricted. Run the
organization's ingestion worker (see below) and wait for Ready, then use Edit
access to grant a person, a group, or the whole organization permission to
read. Managing a document does not itself grant reading access.

Sign in as the granted member and open Chat. Answers show filename/location
citations; View query trace shows the caller's stage decisions and timings.
After revocation, new questions cannot use that evidence. Existing answers
are not retroactively erased from an open session. Generation requires the
server configuration below. Without permitted evidence, the app abstains.

## Upload and ask through the API

Log in through `POST /api/v1/auth/login` in the API docs and use its access token
with the **Authorize** button. Owners and admins can upload and manage documents.
New uploads are restricted: even the uploader needs a direct/group grant or
organization visibility before the document can be used in an answer.

Uploads accept raw file bytes, not multipart forms. TXT, Markdown, DOCX, and
text PDFs are supported up to 25 MiB. Encrypted or image-only PDFs are rejected;
there is no OCR. Extraction has a separate four-million-character limit and
DOCX expansion limits. PDF parsing runs in a separate process with a 256 MiB
memory cap and a 30-second deadline, plus a 16 MiB page-content decoding budget.
Those limits can reject otherwise valid complex PDFs. Chunks preserve page,
paragraph, or starting-line locations.

Here is a PowerShell example from a folder containing your `notes.txt`:

```powershell
$RagelitHeaders = @{ Authorization = 'Bearer <access_token>' }
$Document = Invoke-RestMethod -Method Post -Headers $RagelitHeaders `
  -Uri 'http://localhost:8000/api/v1/documents?filename=notes.txt' `
  -ContentType 'application/octet-stream' -InFile .\notes.txt
Invoke-RestMethod -Method Patch -Headers $RagelitHeaders `
  -Uri "http://localhost:8000/api/v1/documents/$($Document.id)" `
  -ContentType 'application/json' `
  -Body '{"visibility":"organization","user_ids":[],"group_ids":[]}'
```

Get the organization UUID from `GET /api/v1/organizations`. Start a worker in a
separate terminal from `backend`, replacing the placeholder with that UUID:

```console
uv run python -m app.workers.ingestion --organization-id <organization_uuid>
```

The first run downloads public embedding models into the ignored
`data/models` cache. Dense embeddings use `BAAI/bge-small-en-v1.5` (384 dimensions);
sparse retrieval uses `Qdrant/bm25`. Document bodies are embedded locally. The
worker uses the scoped application database role and persists jobs, leases,
retry counts, and safe failure codes. Run one worker per organization; add
`--once` to process at most one job.

Poll `GET /api/v1/documents/{id}` until its state is `ready`, then send:

```powershell
Invoke-RestMethod -Method Post -Headers $RagelitHeaders `
  -Uri 'http://localhost:8000/api/v1/chat/query' `
  -ContentType 'application/json' -Body '{"question":"What do these notes say?"}'
```

Answer generation needs an already-running compatible chat-completions server.
Set `RAGELIT_LLM_BASE_URL` to its API base URL (for example,
`http://127.0.0.1:8080/v1`) and `RAGELIT_LLM_MODEL` to its model name, then restart
the API. Set `RAGELIT_LLM_API_KEY` only if that server requires one. No provider
login or credentials are discovered automatically. An external endpoint receives
the question and permitted context, so choose it deliberately for private data.

Generation is disabled by default. With no permitted evidence, the API abstains
without calling a model. With evidence but no configured endpoint, it returns
`generation_not_configured` and a trace ID. Responses include citation IDs and
source locations; checking those IDs does not prove every claim is correct.
`GET /api/v1/query-runs/{id}` exposes stage decisions and timings only to the
querying user. Traces do not store questions, answers, or document bodies.

## Document lifecycle

`PATCH /documents/{id}` replaces visibility and the direct-user/group grant sets.
Grant targets must belong to the active organization. Retrieval reloads current
membership and grants, then applies tenant, active-version, and grant filters
before both vector and keyword search. Owners do not get implicit content access.

`POST /documents/{id}/retry` retries a failed version. Upload a replacement to
`POST /documents/{id}/versions?filename=notes.txt`; successful processing
supersedes the old version. `GET /documents/{id}/versions` lists processing states
and chunk counts. These paths use the `/api/v1` prefix.

Deletion is soft and removes searchability. Source files and inactive vectors
remain for recovery; there is no retention purge yet. Re-uploading identical
bytes in the same organization returns the existing document, while bytes
belonging to a deleted document return a conflict. A failed projection update
returns `503` and queues repair instead of reporting success.

## Verify

Tests need disposable PostgreSQL at `127.0.0.1:5432` (database, user, and password
all `postgres`) and Qdrant at `127.0.0.1:6333`. Do not use valuable databases:
browser tests recreate the dedicated `ragelit_e2e` database and collection.
Stop development services before starting
these test containers:

```console
docker compose stop postgres qdrant
docker run --rm --name ragelit-postgres-test -e POSTGRES_PASSWORD=postgres -p 127.0.0.1:5432:5432 -d postgres:18-alpine
docker run --rm --name ragelit-qdrant-test -p 127.0.0.1:6333:6333 -d qdrant/qdrant:v1.15.4
```

Run `powershell -File scripts/verify.ps1` on Windows or
`bash scripts/verify.sh` on macOS/Linux. Both scripts check Compose, backend formatting
and types, PostgreSQL/Qdrant tests, generated contracts, the frontend build, and
browser journeys. Browser tests use the real API, worker, PostgreSQL, and Qdrant
with test-only four-dimensional embeddings and an extractive generator. They
check access boundaries and UI behavior, not relevance or real-model answer
quality. Fixtures use a separate temporary upload directory and require the
dedicated local test targets. These tests do not call a paid model.

Both scripts also validate separate access-control, injection, and isolation
report exports before checking the frontend. Each run needs fresh destinations;
existing export files and directories are preserved. The Windows script runs
its native export regression tests before the backend suites.

On 2 October 2026, `powershell -NoProfile -File scripts/verify.ps1` exited 0
on Windows with local PostgreSQL 16 and Qdrant 1.15.4: 65 unit checks,
156 integration/API checks, and 41 browser tests. Static checks,
generated-contract drift, and the production build also passed. This does
not verify Linux/PostgreSQL 18 CI or the quality of a real model.

```console
docker stop ragelit-postgres-test ragelit-qdrant-test
```

CI scans for secrets and audits both committed lockfiles with OSV 2.6.0.
The workflow downloads public npm and PyPI vulnerability databases, then
scans offline without sending the dependency inventory to an external API.
Missing inputs, invalid advisory records, scanner errors, and known
vulnerabilities fail the check. The audit does not establish exploitability.

## Audit jobs in the portal

Open Audits and choose Start synthetic audit. The browser can queue jobs, but
the operator must run the separate worker for that organization. Member accounts
have no audit access. Queued and running outcomes stay unknown; finished exits
0, 1, and 2 mean pass, fail, and inconclusive. A pass applies only to the synthetic
safe pack, not company documents.

Use the application's non-owner database URL for job storage. Give audit
bootstrap settings only to this worker terminal, not the web server. From
`backend`:

```powershell
$env:RAGELIT_DATABASE_URL = 'postgresql+psycopg://ragelit_app:ragelit_app@127.0.0.1:5432/ragelit'
$env:RAGELIT_AUDIT_ENVIRONMENT = 'local'
$env:RAGELIT_AUDIT_DATABASE_ADMIN_URL = 'postgresql+psycopg://ragelit_owner:ragelit_owner@127.0.0.1:5432/postgres'
$env:RAGELIT_AUDIT_QDRANT_URL = 'http://127.0.0.1:6333'
$env:RAGELIT_AUDIT_ROOT = Join-Path (Split-Path -Parent $PWD.Path) 'data/audit-workspaces'
$env:RAGELIT_AUDIT_APPLICATION_PASSWORD = [guid]::NewGuid().ToString('N')
$env:RAGELIT_AUDIT_FIXTURE_PASSWORD = [guid]::NewGuid().ToString('N')
uv run --frozen python -m app.workers.audit --organization-id <organization-uuid> --once
```

Obtain the organization's UUID from `GET /api/v1/organizations` in the API docs.
Replace the angle-bracket placeholder before running the command. Omit `--once`
to keep the worker waiting for that organization's jobs. The worker derives a
fresh database, collection, and directory from each request UUID beneath the
configured root. It runs the full safe profile with no paid provider.
Execution is capped at 1800 seconds. Saved JSON downloads preserve the validated
artifact bytes and SHA-256; case evidence distinguishes candidates from delivered
output and observed stages from stages that were never reached.

Choose Download HTML on a saved audit to open its evidence offline. The export
preserves the JSON outcome, coverage limits, first exposure and redacted stage
observations. It includes no scripts or remote assets. HTML is a readable view,
not a new audit or a replacement for the original JSON checksum.

After sleep or an interrupted lease, new runs stay blocked for that organization.
First stop and confirm both the worker and its child have stopped. Then recover
the exact request ID with the same job database URL and organization UUID:

```console
uv run --frozen python -m app.workers.audit --organization-id <organization-uuid> --recover-run <request-uuid> --confirm-worker-stopped
```

Recovery retains fixtures and any report, finishes inconclusively, and returns
exit 2. It does not delete, rerun, or convert the audit to pass.

## Synthetic access-control CLI

The audit CLI tests the real API, ingestion worker, PostgreSQL RLS, and Qdrant
against generated documents. It uses deterministic embeddings and answers;
no paid model is called. This checks access boundaries, not real-model quality
or security certification. Retrieval observations cover fused results, not
internal dense or sparse prefetch candidates.

From `backend`, set these process-local variables for disposable local services:

```powershell
$AuditName = 'ragelit_audit_' + [guid]::NewGuid().ToString('N')
$env:RAGELIT_AUDIT_ENVIRONMENT = 'local'
$env:RAGELIT_AUDIT_DATABASE_ADMIN_URL = "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/$AuditName"
$env:RAGELIT_AUDIT_QDRANT_URL = 'http://127.0.0.1:6333'
$env:RAGELIT_AUDIT_ROOT = Join-Path (Split-Path -Parent $PWD.Path) "data/audit-workspaces/$AuditName"
$env:RAGELIT_AUDIT_APPLICATION_PASSWORD = [guid]::NewGuid().ToString('N')
$env:RAGELIT_AUDIT_FIXTURE_PASSWORD = [guid]::NewGuid().ToString('N')
uv run --frozen python -m app.audits.cli
```

Ordinary application settings are ignored. The CLI requires a loopback database
whose name starts with `ragelit_audit_` and a matching workspace directory. It
refuses unowned resources, changed ownership markers, and interrupted fixtures;
it does not reset an existing workspace automatically. Choose a fresh name after
an interrupted run. Audit resources remain local for inspection.

A full safe run returns 0 only when all 51 cases pass. Completed control failures
return 1. Missing evidence, runtime errors, partial runs, and report-write failures
return 2. Use `--case org-1:organization` for a partial run; fixture preparation
still prepares the whole pack. Normally completed partial runs mark unused
instances as skipped, so the workspace can be used again. Actual interruptions
remain incomplete and cannot be silently reused. Deliberately broken controls
require `--lab`:

```console
uv run --frozen python -m app.audits.cli --profile vulnerable --lab
uv run --frozen python -m app.audits.cli --profile deny_all --lab
```

Both broken profiles should return 1 with complete coverage. The vulnerable
profile demonstrates raw retrieval exposure and later containment, not
necessarily disclosure to the user. Deny-all proves that rejecting every query
cannot earn a passing audit.

Reports and SHA-256 receipts are written under the workspace's `reports` directory.
They omit document bodies, questions, answers, tokens, and passwords. Verification
scripts export only the three full-profile reports and their receipts to a fresh
directory under `data/audit-reports`, then validate coverage, redaction, and the
literal 0/1/1 gates before CI uploads them. Existing export destinations are
refused. Set `RAGELIT_AUDIT_EXPORT_DIRECTORY` to choose another fresh destination.
Keep the laptop awake during service-backed checks; sleep counts against subprocess
timeouts. The CLI also remains available separately from the portal.

## Document-instruction audit

The injection CLI runs six owned-document cases through the real chat API.
Its deterministic profiles check the harness, not a model's resistance.
Use a fresh audit name and matching root with the variables above. Do not reuse
an access-control workspace: the injection pack has different fixture bindings.

```console
uv run --frozen python -m app.audits.injection_cli --profile resistant
uv run --frozen python -m app.audits.injection_cli --profile obeying
uv run --frozen python -m app.audits.injection_cli --profile deny_all
```

These return 0, 1 and 1 respectively when evidence is complete. Use
`--trials <count>` with an integer from 1 to 20 to repeat each tenant's benign
and attack cases (default 1).
An abstention is excluded from the attack denominator and fails answer utility.
No evaluated attacks means a null rate, not zero. Incomplete evidence or a
runtime/report-write failure returns 2 and cannot establish resistance.

Reports use a separate redacted schema with original-byte SHA-256 receipts.
Validate one with `--validate-report <report.json>`, or validate an exported
three-profile pack with `--validate-reports <directory>`. CI uses a fresh
`data/injection-reports` directory, separate from access-control artifacts;
`RAGELIT_INJECTION_EXPORT_DIRECTORY` selects another fresh destination.

An optional local-model run requires `--profile local`, `--local-base-url`,
`--model` and `--weights-sha256`. Only numeric loopback HTTP `/v1` endpoints
are accepted, with no API key. It records the declared weights hash and system
prompt hash; it does not attest that the server loaded those weights.
See [commands and measured limits](docs/verification/2026-10-05-injection-pack.md).

## Storage isolation comparison

The comparison CLI runs the same 51 access-control cases against shared
pre-filtering, owned tenant collections and a lab-only post-filter baseline.
Tenant routing changes actual ingestion and collection lifecycle, not just a
payload flag. It does not change the portal's default storage.

Use the audit variables above with a fresh name and matching root before each
strategy:

```console
uv run --frozen python -m app.audits.isolation_cli --strategy shared_pre_filter
uv run --frozen python -m app.audits.isolation_cli --strategy tenant_collections
uv run --frozen python -m app.audits.isolation_cli --strategy lab_post_filter --lab
```

Shared and tenant runs should return 0; the explicit lab baseline should return
1 with raw retrieval exposure. Runtime failures or incomplete evidence return 2.
Reports and receipts use a separate schema and fresh export directory. Validate
one report with `--validate-report <report.json>`, or all three with
`--validate-reports <directory>`. CI exports to `data/isolation-reports`;
`RAGELIT_ISOLATION_EXPORT_DIRECTORY` selects another fresh destination.
These runs use deterministic providers and are not quality benchmarks.
See [recorded checks and limits](docs/verification/2026-10-05-isolation-comparison.md).

Billing, invitations, streaming and retrieval benchmarks remain separate work.
