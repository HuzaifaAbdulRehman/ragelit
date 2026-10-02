# RAGelit

RAGelit is a multi-tenant document assistant in active development. Owners and
admins can manage groups, upload documents, and edit reading access in one web
portal. Members can ask questions over permitted evidence and inspect cited
answers or their own query traces.

Automated privacy audits, retrieval benchmarks, and live streaming are still
planned. This is a development build, not a production deployment.

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

Billing, invitations, automated audits, streaming, and benchmark comparisons
remain separate milestones.
