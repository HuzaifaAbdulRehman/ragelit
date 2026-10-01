# RAGelit

RAGelit is a multi-tenant document assistant in active development. The current
milestone provides the secure application foundation: login, rotating refresh
sessions, organization switching, role-based management, forced PostgreSQL row
security, and a small web workspace.

Document upload, retrieval, chat, and privacy audits are not implemented yet.

## Prerequisites

- Docker Desktop with Compose
- Python 3.12 or 3.13
- [uv](https://docs.astral.sh/uv/)
- Node.js 22 and npm

## Run locally

Copy the example configuration from the repository root:

```powershell
Copy-Item .env.example .env
```

On macOS or Linux, use `cp .env.example .env` instead. The example credentials
are only for local development.

Start PostgreSQL and Qdrant:

```console
docker compose up -d postgres qdrant
```

Prepare and start the API:

```console
cd backend
uv sync --frozen
uv run alembic upgrade head
uv run python -m app.seed
uv run uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port 8000
```

The seed command prints a random password for each newly created demo user. It
does not print or reset credentials when run again. Keep the first output if you
want to use the demo accounts.

In another terminal, start the web app:

```console
cd frontend
npm ci
npm run dev
```

Open <http://localhost:5173/login>. API documentation is available at
<http://localhost:8000/docs>.

## Verify

The test suite expects a disposable PostgreSQL server at `127.0.0.1:5432` with
the database, user, and password all set to `postgres`. Do not point it at a
database containing useful data because the browser tests recreate a database
named `ragelit_e2e`.

If the development database is running, stop it first so port 5432 is free.
Then start the test server:

```console
docker compose stop postgres
docker run --rm --name ragelit-postgres-test -e POSTGRES_PASSWORD=postgres -p 5432:5432 -d postgres:18-alpine
```

Then run the verification entry point for your operating system:

```powershell
powershell -File scripts/verify.ps1
```

```console
bash scripts/verify.sh
```

Both scripts check Compose configuration, backend formatting and types, unit
and PostgreSQL tests, the generated API client, the frontend build, and the live
browser journeys. Run `docker stop ragelit-postgres-test` when finished.

## Current scope

- One web portal can serve multiple organizations.
- Roles are owner, admin, auditor, and member.
- The API enforces tenant boundaries; role-aware navigation is only a usability
  aid.
- Access tokens stay in browser memory. Rotating refresh sessions use an
  HTTP-only cookie.
- Demo identities use synthetic addresses and local-only passwords.
- Billing, invitations, document ingestion, retrieval, answer generation, and
  privacy auditing remain future milestones.
