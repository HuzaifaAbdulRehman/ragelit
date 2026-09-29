# Secure Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a clean-clone application shell whose authentication,
membership, role, and PostgreSQL tenant boundaries are tested before document
or RAG features are added.

**Architecture:** Adapt the MIT-licensed FastAPI full-stack template for
commodity project structure, authentication primitives, generated API client,
and browser tests. Replace its single-user item model with RAGelit-owned
organization, membership, group, session, access-scope, and row-level security
modules. The FastAPI dependency chain validates the signed token, loads the
current session and membership, sets PostgreSQL tenant context, and only then
enters a protected handler.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic,
PostgreSQL, PyJWT, pwdlib with Argon2, React, TypeScript, Vite, TanStack Query,
TanStack Router, Playwright, pytest, Docker Compose

**Spec:** `docs/superpowers/specs/2026-09-29-ragelit-security-design.md`

## Global Constraints

- `references/` remains untracked and is never a build input.
- Add the FastAPI template source, commit, MIT license, and adapted file list to
  `THIRD_PARTY_NOTICES.md` before committing adapted code.
- Python support is `>=3.12,<3.14`; do not inherit the reference template's
  Python 3.14 floor.
- The browser never stores refresh tokens in local storage. Refresh tokens use
  an HttpOnly, SameSite=Lax cookie and are Secure outside local development.
- Access tokens expire after 15 minutes. Refresh sessions expire after 14 days
  and rotate on every use.
- Tokens identify a user, organization, and session, but current membership and
  role are loaded from PostgreSQL on every protected request.
- Client-provided role, group, or membership values have no effect.
- Tenant-owned PostgreSQL tables use forced row-level security.
- Application queries use a non-owner, non-superuser PostgreSQL role.
- A missing tenant context causes tenant-owned queries to return no rows or
  fail; it never opens access.
- Owners and admins do not gain document-read access through this milestone.
- Use UUID primary keys and UTC timestamps.
- API errors follow RFC 9457 problem details with stable `code` values.
- Every task uses a failing test before implementation and ends with a local
  commit no longer than 50 characters in imperative mood.

## Review Focus

- A valid access token names an organization where the user is no longer an
  active member: `get_current_principal()` returns `401 membership_inactive`.
  Task 5 owns this test.
- A caller sends another organization's identifier to a protected route: the
  route returns `404` without revealing whether the resource exists. Task 8
  owns this test.
- A refresh token is replayed after rotation: the whole session is revoked and
  refresh returns `401 refresh_reused`. Task 4 owns this test.
- A protected query runs without `SET LOCAL app.organization_id`: forced RLS
  exposes zero tenant rows. Task 7 owns this test.
- An admin asks for member-only content in a later milestone: the role grants
  control-plane actions only and `AccessScope` contains groups, not automatic
  content permission. Task 6 owns the policy test that pins this distinction.

## Planned file map

```text
.env.example                         local configuration contract
.gitignore                           generated, secret, and data exclusions
compose.yml                          PostgreSQL, Qdrant, API, worker, web
THIRD_PARTY_NOTICES.md               adapted source ledger
backend/pyproject.toml               Python dependencies and quality tools
backend/alembic.ini                  migration configuration
backend/app/main.py                  FastAPI application factory
backend/app/api/router.py            versioned router composition
backend/app/api/deps.py              database and principal dependencies
backend/app/core/config.py           validated environment settings
backend/app/core/problems.py         stable problem-detail responses
backend/app/core/security.py         password and token primitives
backend/app/db/base.py               SQLAlchemy declarative base
backend/app/db/session.py            engines, sessions, transaction helpers
backend/app/identity/models.py       users and refresh sessions
backend/app/identity/schemas.py      login, token, and current-user schemas
backend/app/identity/service.py      login, refresh, logout, session checks
backend/app/identity/api.py          authentication routes
backend/app/tenancy/models.py        organizations, memberships, groups
backend/app/tenancy/enums.py         persisted role values
backend/app/tenancy/policy.py        Role, Action, and role_allows
backend/app/tenancy/scope.py         RequestPrincipal and AccessScope builders
backend/app/tenancy/rls.py           PostgreSQL request-local tenant context
backend/app/tenancy/service.py       organization, member, and group operations
backend/app/tenancy/api.py           protected tenancy routes
backend/app/alembic/                  schema and RLS migrations
backend/app/seed.py                  deterministic local demo identities
backend/tests/unit/                   pure policy, token, and schema tests
backend/tests/integration/            PostgreSQL and API boundary tests
frontend/package.json                web dependencies and scripts
frontend/src/main.tsx                web entrypoint
frontend/src/api/                     generated client and auth transport
frontend/src/features/auth/           login and session state
frontend/src/features/shell/          navigation and organization switcher
frontend/tests/                       Playwright journeys
.github/workflows/ci.yml              backend, frontend, and container checks
```

### Task 1: Adapt the repository skeleton and record attribution

**Files:**
- Create: `.gitignore`
- Create: `.env.example`
- Create: `compose.yml`
- Create: `THIRD_PARTY_NOTICES.md`
- Create: `backend/pyproject.toml`
- Create: `backend/app/__init__.py`
- Create: `backend/tests/test_repository_contract.py`
- Create: `frontend/package.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/vite.config.ts`

**Interfaces:**
- Consumes: FastAPI full-stack template commit
  `cb740b656d7a0a6c5e12c7bf8e50343ec94ee9c7` under the MIT license.
- Produces: `uv run pytest`, `npm run check`, and Docker Compose project
  `ragelit`; later tasks rely on these command names.

- [ ] **Step 1: Write the repository contract test**

```python
def test_reference_sources_are_not_tracked(repo_root: Path) -> None:
    tracked = subprocess.check_output(
        ["git", "ls-files", "references"], cwd=repo_root, text=True
    )
    assert tracked == ""


def test_third_party_notice_records_template_commit(repo_root: Path) -> None:
    notice = (repo_root / "THIRD_PARTY_NOTICES.md").read_text()
    assert "cb740b656d7a0a6c5e12c7bf8e50343ec94ee9c7" in notice
    assert "MIT" in notice
```

- [ ] **Step 2: Run the test and verify the missing files fail**

Run: `cd backend && uv run pytest tests/test_repository_contract.py -v`

Expected: FAIL because the backend project and notice do not exist.

- [ ] **Step 3: Create the minimal project skeleton**

Adapt only the reference template's package layout, lint/test configuration,
generated client setup, and Compose conventions. Exclude email, password reset,
Traefik, Adminer, Sentry, and template item features.

- [ ] **Step 4: Add the attribution ledger**

`THIRD_PARTY_NOTICES.md` must name the source URL, exact commit, MIT license,
adapted files, and that RAGelit modified them.

- [ ] **Step 5: Run repository checks**

Run: `cd backend && uv run pytest tests/test_repository_contract.py -v`

Expected: PASS.

Run: `cd frontend && npm run check`

Expected: PASS with no TypeScript or formatting errors.

- [ ] **Step 6: Commit**

```bash
git add .gitignore .env.example compose.yml THIRD_PARTY_NOTICES.md backend frontend
git commit -m "scaffold application workspace"
```

### Task 2: Add validated configuration and health endpoints

**Files:**
- Create: `backend/app/core/config.py`
- Create: `backend/app/core/problems.py`
- Create: `backend/app/main.py`
- Create: `backend/app/api/router.py`
- Create: `backend/app/api/routes/health.py`
- Create: `backend/tests/unit/core/test_config.py`
- Create: `backend/tests/api/test_health.py`

**Interfaces:**
- Consumes: Task 1 application package and test commands.
- Produces: `Settings`, `create_app() -> FastAPI`, `GET /health/live`, and
  `GET /health/ready`.

- [ ] **Step 1: Write configuration tests**

```python
def test_production_rejects_default_secret() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production", secret_key="change-me", ...)


def test_upload_limit_is_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(max_upload_bytes=0, ...)
```

- [ ] **Step 2: Run the configuration tests**

Run: `cd backend && uv run pytest tests/unit/core/test_config.py -v`

Expected: FAIL because `Settings` is missing.

- [ ] **Step 3: Implement `Settings`**

Define database admin and application URLs, Qdrant URL, secret key,
environment, access-token minutes, refresh-session days, upload limit, allowed
origins, and cookie security. Validate environment-specific secrets and limits.

- [ ] **Step 4: Write health endpoint tests**

```python
def test_liveness_has_no_dependency_checks(client: TestClient) -> None:
    assert client.get("/health/live").json() == {"status": "ok"}


def test_readiness_reports_failed_dependency(client, failing_checks) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["code"] == "dependency_unavailable"
```

- [ ] **Step 5: Implement the app factory and health router**

`create_app(settings: Settings | None = None) -> FastAPI` registers problem
handlers and the versioned API router. Readiness accepts injected PostgreSQL and
Qdrant check callables so unit tests need no services.

- [ ] **Step 6: Verify**

Run: `cd backend && uv run pytest tests/unit/core/test_config.py tests/api/test_health.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app backend/tests
git commit -m "add configuration and health checks"
```

### Task 3: Create identity and tenancy models

**Files:**
- Create: `backend/app/db/base.py`
- Create: `backend/app/db/session.py`
- Create: `backend/app/identity/models.py`
- Create: `backend/app/tenancy/enums.py`
- Create: `backend/app/tenancy/models.py`
- Create: `backend/app/alembic/env.py`
- Create: `backend/app/alembic/versions/0001_identity_tenancy.py`
- Create: `backend/tests/unit/db/test_model_constraints.py`
- Create: `backend/tests/integration/db/test_initial_migration.py`

**Interfaces:**
- Consumes: Task 2 `Settings.database_admin_url` and
  `Settings.database_url`.
- Produces: SQLAlchemy models `User`, `RefreshSession`, `Organization`,
  `Membership`, `Group`, and `GroupMember`; persisted enum `Role` with values
  `owner`, `admin`, `auditor`, and `member`.

- [ ] **Step 1: Write model constraint tests**

Test these exact constraints: normalized unique email, unique organization
slug, one membership per `(user_id, organization_id)`, unique group name per
organization, one group-member pair, UUID primary keys, and UTC timestamps.

- [ ] **Step 2: Run the unit tests**

Run: `cd backend && uv run pytest tests/unit/db/test_model_constraints.py -v`

Expected: FAIL because the models are missing.

- [ ] **Step 3: Implement the models and naming convention**

Use SQLAlchemy 2 typed mappings. `GroupMember` and every refresh-token row carry
`organization_id` directly so row-level policy and integrity checks do not
depend on an optional join. A refresh-token row also records `family_id`,
`token_hash`, `used_at`, `replaced_by_id`, `expires_at`, and `revoked_at` so
replay remains detectable after rotation.

- [ ] **Step 4: Write the migration integration test**

The test upgrades an empty test database to `head`, inspects all six tables,
checks foreign keys and unique constraints, downgrades to `base`, then upgrades
again.

- [ ] **Step 5: Generate and review migration `0001`**

Do not accept generated cascade behavior without comparing every foreign key
to the design. Users are retained when a membership is deleted; organization
deletion cascades memberships and groups only in local development until the
document lifecycle exists.

- [ ] **Step 6: Verify**

Run: `cd backend && uv run pytest tests/unit/db/test_model_constraints.py tests/integration/db/test_initial_migration.py -v`

Expected: PASS against PostgreSQL.

- [ ] **Step 7: Commit**

```bash
git add backend/app/db backend/app/identity backend/app/tenancy backend/app/alembic backend/tests
git commit -m "add identity and tenancy schema"
```

### Task 4: Implement password login and rotating sessions

**Files:**
- Create: `backend/app/core/security.py`
- Create: `backend/app/identity/schemas.py`
- Create: `backend/app/identity/service.py`
- Create: `backend/app/identity/api.py`
- Create: `backend/tests/unit/identity/test_security.py`
- Create: `backend/tests/integration/identity/test_sessions.py`
- Create: `backend/tests/api/test_auth.py`

**Interfaces:**
- Consumes: Task 3 `User`, `RefreshSession`, `Organization`, and `Membership`.
- Produces:
  `hash_password(password: str) -> str`,
  `verify_password(password: str, encoded: str) -> bool`,
  `issue_access_token(user_id: UUID, organization_id: UUID, session_id: UUID) -> str`,
  `login(command: LoginCommand) -> LoginResult`,
  `refresh(raw_token: str) -> RefreshResult`, and
  `logout(session_id: UUID) -> None`.

- [ ] **Step 1: Write password and token unit tests**

Assert Argon2 hashes do not equal the password, wrong passwords fail, access
tokens carry `sub`, `org`, `sid`, `iat`, and `exp`, expired tokens fail, and no
role or group claim is present.

- [ ] **Step 2: Run the unit tests**

Run: `cd backend && uv run pytest tests/unit/identity/test_security.py -v`

Expected: FAIL because the security functions are missing.

- [ ] **Step 3: Implement password and access-token primitives**

Use `pwdlib` with Argon2 and PyJWT with one configured algorithm. Do not log
raw credentials or token decode errors containing token material.

- [ ] **Step 4: Write session integration tests**

Test successful login, invalid password, inactive user, inactive membership,
14-day expiry, refresh rotation, logout revocation, and replay of a rotated
refresh token. Replay must revoke the session family and return
`refresh_reused`.

- [ ] **Step 5: Implement session storage and rotation**

Generate refresh tokens with `secrets.token_urlsafe(32)` and store only a
SHA-256 hash. Rotate in one transaction using a row lock. Return the new raw
token only to the cookie-setting API layer.

- [ ] **Step 6: Add auth routes**

Implement `POST /api/v1/auth/login`, `POST /api/v1/auth/refresh`, and
`POST /api/v1/auth/logout`. Login accepts email, password, and organization
slug. Refresh reads only the HttpOnly cookie.

- [ ] **Step 7: Verify**

Run: `cd backend && uv run pytest tests/unit/identity tests/integration/identity tests/api/test_auth.py -v`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/core/security.py backend/app/identity backend/tests
git commit -m "add rotating login sessions"
```

### Task 5: Build the current principal and access scope

**Files:**
- Create: `backend/app/api/deps.py`
- Create: `backend/app/tenancy/policy.py`
- Create: `backend/app/tenancy/scope.py`
- Create: `backend/tests/unit/tenancy/test_policy.py`
- Create: `backend/tests/integration/tenancy/test_principal.py`

**Interfaces:**
- Consumes: Task 4 access-token claims and Task 3 current database rows.
- Produces immutable dataclasses:

```python
@dataclass(frozen=True, slots=True)
class RequestPrincipal:
    user_id: UUID
    organization_id: UUID
    session_id: UUID
    membership_id: UUID
    role: Role


@dataclass(frozen=True, slots=True)
class AccessScope:
    user_id: UUID
    organization_id: UUID
    membership_id: UUID
    role: Role
    group_ids: tuple[UUID, ...]
```

Also produces `get_current_principal()` and `build_access_scope()` FastAPI
dependencies.

- [ ] **Step 1: Write the role matrix tests**

Import the `Role` values fixed in Task 3. Pin actions
`organization_manage`, `members_manage`, `groups_manage`, `documents_manage`,
`audits_run`, and `chat_use`. Assert that admin actions do not imply a document
read grant.

- [ ] **Step 2: Run the policy tests**

Run: `cd backend && uv run pytest tests/unit/tenancy/test_policy.py -v`

Expected: FAIL because `Role`, `Action`, and `role_allows` are missing.

- [ ] **Step 3: Implement the role matrix**

Use a fixed mapping, not role-name comparisons scattered through handlers.
`role_allows(role: Role, action: Action) -> bool` is the only pure policy
function.

- [ ] **Step 4: Write principal integration tests**

Assert token organization mismatch, revoked session, inactive user, inactive
membership, missing membership, and changed role are resolved from current
database state. A role change must affect the next request without issuing a
new access token.

- [ ] **Step 5: Implement principal and scope dependencies**

Decode the token, load the active session, user, and membership, then load
current group IDs. Ignore similarly named headers and request fields.

- [ ] **Step 6: Verify**

Run: `cd backend && uv run pytest tests/unit/tenancy/test_policy.py tests/integration/tenancy/test_principal.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/deps.py backend/app/tenancy backend/tests
git commit -m "derive request access scope"
```

### Task 6: Enforce control-plane actions

**Files:**
- Modify: `backend/app/api/deps.py`
- Create: `backend/tests/api/test_action_guards.py`

**Interfaces:**
- Consumes: Task 5 `RequestPrincipal`, `Action`, and `role_allows`.
- Produces:
  `require_action(action: Action) -> Callable[..., RequestPrincipal]`.

- [ ] **Step 1: Write parameterized guard tests**

For every role and action pair, assert the fixed matrix. Missing permission
returns problem code `action_forbidden`; an invalid or inactive principal still
returns the authentication error from Task 5.

- [ ] **Step 2: Run the guard tests**

Run: `cd backend && uv run pytest tests/api/test_action_guards.py -v`

Expected: FAIL because `require_action` is missing.

- [ ] **Step 3: Implement the dependency factory**

Return the already validated principal on success. Do not query role fields
from the request or create a second policy map.

- [ ] **Step 4: Verify**

Run: `cd backend && uv run pytest tests/api/test_action_guards.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/deps.py backend/tests/api/test_action_guards.py
git commit -m "enforce role action guards"
```

### Task 7: Force PostgreSQL tenant isolation

**Files:**
- Create: `backend/app/tenancy/rls.py`
- Create: `backend/app/alembic/versions/0002_tenant_rls.py`
- Create: `backend/tests/integration/tenancy/test_rls.py`
- Modify: `compose.yml`
- Modify: `.env.example`

**Interfaces:**
- Consumes: Task 5 validated `RequestPrincipal.organization_id` and
  `RequestPrincipal.user_id`.
- Produces:
  `set_request_context(session: Session, user_id: UUID, organization_id: UUID) -> None`
  using transaction-local PostgreSQL settings `app.organization_id` and
  `app.user_id`.

- [ ] **Step 1: Write RLS integration tests using the application role**

Seed two organizations. Assert no context returns zero tenant-owned rows,
organization A returns only A rows, an A insert with B's organization ID fails,
a user may list only their own active memberships across organizations, and
changing the setting inside one transaction cannot leak into the next pooled
connection.

- [ ] **Step 2: Run the RLS tests**

Run: `cd backend && uv run pytest tests/integration/tenancy/test_rls.py -v`

Expected: FAIL because policies and the application role are missing.

- [ ] **Step 3: Add owner and application database roles**

Compose and `.env.example` expose separate migration and runtime URLs. The
runtime role is neither table owner nor superuser and has only required grants.

- [ ] **Step 4: Add forced row policies**

Migration `0002` enables and forces RLS on memberships, groups, group members,
and refresh sessions. Organization administration uses the organization
setting. The organization switcher may also select membership rows whose
`user_id` equals `app.user_id`. Policies use
`current_setting(..., true)` and fail closed when their required value is
unset.

- [ ] **Step 5: Set context inside protected transactions**

After verifying the JWT signature, use its user and organization claims only to
set a restrictive transaction context. Then load the session and current
membership and build the validated principal. Login sets context from the
password-verified user and requested organization before reading membership.
Use `SET LOCAL`, not connection-global `SET`; signed claims scope the lookup but
never replace the membership check.

- [ ] **Step 6: Verify**

Run: `cd backend && uv run pytest tests/integration/tenancy/test_rls.py -v`

Expected: PASS as the runtime application role.

- [ ] **Step 7: Commit**

```bash
git add compose.yml .env.example backend/app/tenancy/rls.py backend/app/alembic backend/tests
git commit -m "force tenant row isolation"
```

### Task 8: Add organization, member, and group APIs

**Files:**
- Create: `backend/app/tenancy/schemas.py`
- Create: `backend/app/tenancy/service.py`
- Create: `backend/app/tenancy/api.py`
- Modify: `backend/app/api/router.py`
- Create: `backend/tests/api/test_organizations.py`
- Create: `backend/tests/api/test_members.py`
- Create: `backend/tests/api/test_groups.py`
- Modify: `backend/app/identity/service.py`
- Modify: `backend/app/identity/api.py`
- Modify: `backend/tests/api/test_auth.py`

**Interfaces:**
- Consumes: Tasks 5 through 7 principal, action guard, session, and RLS
  context.
- Produces organization membership listing, member role and activation
  changes, group CRUD, group membership APIs, and authenticated organization
  switching under `/api/v1`.

- [ ] **Step 1: Write tenant-negative API tests**

For each route, seed organizations A and B, authenticate as A, and pass B's
identifier. Assert `404 resource_not_found` and that the response never contains
B's name, user email, group name, or identifier.

- [ ] **Step 2: Write action-positive and action-negative tests**

Owners and admins may manage members and groups. Auditors and members may not.
No actor may deactivate the last active owner. A user may list only their own
active organization memberships for the switcher.

Switching accepts a target organization ID, derives the user from the current
principal, verifies an active target membership, revokes the old refresh-token
family, and issues a new family scoped to the target organization. Add positive,
inactive-membership, cross-user, and replay tests.

- [ ] **Step 3: Run the API tests**

Run: `cd backend && uv run pytest tests/api/test_organizations.py tests/api/test_members.py tests/api/test_groups.py -v`

Expected: FAIL because services and routes are missing.

- [ ] **Step 4: Implement service functions**

Use explicit functions `list_my_organizations`, `list_members`,
`change_member_role`, `set_member_active`, `create_group`, `rename_group`,
`delete_group`, `add_group_member`, and `remove_group_member`. Each consumes the
current principal and never accepts an effective tenant separately from the
route check. Add
`switch_organization(principal: RequestPrincipal, target_id: UUID) -> RefreshResult`
to the identity service.

- [ ] **Step 5: Implement routes and generated schemas**

Return public user fields only. Use stable pagination and problem codes. Update
the OpenAPI snapshot so the frontend client can be generated in Task 9.

- [ ] **Step 6: Verify**

Run: `cd backend && uv run pytest tests/api/test_organizations.py tests/api/test_members.py tests/api/test_groups.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/tenancy backend/app/identity backend/app/api/router.py backend/tests/api
git commit -m "add tenant management APIs"
```

### Task 9: Seed demo identities and build the login shell

**Files:**
- Create: `backend/app/seed.py`
- Create: `backend/tests/integration/test_seed.py`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/router.tsx`
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/api/generated/`
- Create: `frontend/src/features/auth/AuthProvider.tsx`
- Create: `frontend/src/features/auth/LoginPage.tsx`
- Create: `frontend/src/features/shell/AppShell.tsx`
- Create: `frontend/src/features/shell/OrganizationSwitcher.tsx`
- Create: `frontend/tests/auth.spec.ts`
- Create: `frontend/tests/tenant-navigation.spec.ts`

**Interfaces:**
- Consumes: Task 4 auth routes and Task 8 membership listing.
- Produces `seed_demo(session: Session) -> DemoManifest`, in-memory access-token
  state, cookie-based refresh, protected routes, and organization switching by
  obtaining a token for an existing active membership.

- [ ] **Step 1: Write the idempotent seed test**

Run the seed twice and assert exactly two organizations, four roles in the
first organization, one member in the second, stable slugs, no duplicate
groups, and development credentials emitted only to stdout in explicit demo
mode.

- [ ] **Step 2: Implement the seed command**

Use synthetic domains and names. Refuse to run when `environment=production`.
Do not write plaintext passwords to database rows, files, or logs.

- [ ] **Step 3: Generate the TypeScript API client**

Generate from the running FastAPI OpenAPI document. Hand-written frontend
types must not duplicate generated response models.

- [ ] **Step 4: Write Playwright login and isolation journeys**

Test login failure, owner login, organization list, logout, refresh after page
reload, and manual navigation to another organization's path. The cross-tenant
route must render the generic not-found state.

- [ ] **Step 5: Build auth state and the application shell**

Keep the access token in memory. On initial load, attempt cookie refresh.
Render navigation from current role for usability, while every API call still
depends on backend enforcement.

- [ ] **Step 6: Verify**

Run: `cd backend && uv run pytest tests/integration/test_seed.py -v`

Expected: PASS.

Run: `cd frontend && npm run test -- auth.spec.ts tenant-navigation.spec.ts`

Expected: PASS against the local Compose stack.

- [ ] **Step 7: Commit**

```bash
git add backend/app/seed.py backend/tests frontend
git commit -m "add demo login workspace"
```

### Task 10: Add CI and clean-clone verification

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `scripts/verify.ps1`
- Create: `scripts/verify.sh`
- Create: `backend/tests/test_migration_head.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: all previous task commands.
- Produces one local verification entry point per operating system and one CI
  workflow with equivalent gates.

- [ ] **Step 1: Write the migration-head test**

Assert the database is at the single Alembic head and model metadata produces
no uncommitted schema diff.

- [ ] **Step 2: Add verification scripts**

Scripts run backend format check, lint, types, unit tests, PostgreSQL
integration tests, frontend format check, types, build, Playwright smoke, and
`docker compose config`. Stop at the first failed command and preserve its exit
code.

- [ ] **Step 3: Add GitHub Actions**

Pin action major versions and service image versions. Cache package downloads,
not generated test results. Use no repository secrets for this milestone.

- [ ] **Step 4: Test from a clean local worktree**

Run on Windows: `powershell -File scripts/verify.ps1`

Expected: exit 0 with every named gate passing.

Run in CI/Linux: `bash scripts/verify.sh`

Expected: exit 0 with the same gates.

- [ ] **Step 5: Update the README setup path**

Document exact prerequisites, `.env` creation, Compose startup, migration,
seed, login URLs, test commands, and current limitations. Do not describe RAG
or audit features that are not implemented yet.

- [ ] **Step 6: Commit**

```bash
git add .github scripts backend/tests README.md
git commit -m "verify secure foundation"
```

## Foundation completion gate

Before opening M2 work:

- [ ] `scripts/verify.ps1` passes from a clean Windows worktree.
- [ ] CI passes on Linux without API keys.
- [ ] The application runtime role cannot bypass forced RLS.
- [ ] Token replay, revoked membership, changed role, and cross-tenant route
  tests pass.
- [ ] The UI completes login, refresh, organization selection, and logout.
- [ ] `git ls-files references` returns no output.
- [ ] The attribution ledger matches every adapted file.
- [ ] The README claims only M1 behavior.
