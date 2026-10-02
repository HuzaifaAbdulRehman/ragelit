# Product Experience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make RAGelit usable through one portal for document management, access editing, and cited questions.

**Architecture:** Extend the existing React shell and generated FastAPI client. PostgreSQL remains authoritative for permissions; new read endpoints let editors load existing grants and group members. Browser state is discarded on identity or organization changes, and deterministic browser fixtures exercise the real ingestion and retrieval boundary.

**Tech Stack:** React 19, TypeScript, Vite, TanStack Router and Query, Playwright, FastAPI, SQLAlchemy, PostgreSQL, Qdrant, pytest, uv.

**Spec:** `docs/superpowers/specs/2026-09-29-ragelit-security-design.md`

## Approved delivery scope

On 2 October 2026, the user chose the existing complete-answer API first.
This plan implements the first M3 delivery slice. Chat shows a loading state,
then the validated answer and citations. Live token streaming remains later
work, not a feature claimed by this slice. Audit screens wait for M4's audit
API. Benchmarks and release measurements remain M5.

The API here means RAGelit's FastAPI backend. A configured LLM endpoint is a
separate provider. No hosted provider, credential, or paid account is required
or configured by this plan.

## Global Constraints

- The backend derives organization, role, groups, and grants from the signed session and current database state.
- Administrators do not gain document read access unless a document policy grants it.
- Authorization happens inside Qdrant search; no production path may retrieve broadly and filter afterward.
- PostgreSQL row-level security is enabled and forced for tenant-owned tables.
- The LLM never decides whether a document may be read.
- No Qdrant or policy-store failure may cause an unfiltered fallback.
- Keep access tokens in memory, not local storage, URLs, query keys, or logs.
- Render document names, questions, and model output as text. Do not render untrusted HTML.
- Use the existing dependencies and styles; do not add a UI framework.
- Accept TXT, Markdown, DOCX, and text-based PDF files. Show the default 25 MiB upload limit; the backend's configured limit remains authoritative.
- Questions contain 1 through 4000 trimmed characters; send retrieval limit 10.
- Poll queued and processing documents every 2 seconds only while their page is active.
- Mutations report success only after the backend confirms it. Failed grant updates must not look saved.
- Public invitations, email delivery, provider-settings UI, OCR, conversation persistence, audits, streaming, and benchmarks are excluded.
- Create meaningful local commits. Do not push this branch without an explicit request.

## Review Focus

1. A response from the old organization arrives after switching: it must not populate the new workspace. Task 2 pins this with a delayed-response browser test.
2. A grant target is absent from the current picker page: saving must preserve it unless explicitly removed. Task 5 checks a grant outside the first 50 results.
3. Qdrant fails during an access mutation: the editor must keep the persisted selection and offer retry, never show a success banner. Task 5 exercises a 503.
4. A filename or answer contains HTML: it must be displayed as text without execution. Tasks 4 and 6 use hostile text fixtures.
5. Generation fails after retrieval: the UI must still offer the caller's redacted trace. Task 6 checks a problem response carrying a query-run identifier.

## File boundaries

- `backend/app/documents/access.py`: read current document grants for managers.
- `backend/app/tenancy/service.py` and `api.py`: read group membership using existing tenant guards.
- `frontend/src/api/client.ts`: transport, auth, problem decoding, and raw upload.
- `frontend/src/api/documents.ts`, `tenancy.ts`, `chat.ts`: typed endpoint adapters.
- `frontend/src/features/shell/WorkspaceBoundary.tsx`: scope-local query cache and request cleanup.
- `frontend/src/features/admin/`: people and groups screens.
- `frontend/src/features/documents/`: list, upload, detail, and grant editor.
- `frontend/src/features/chat/`: question form, answer, citations, and safe trace.
- `backend/tests/e2e_app.py` and `e2e_worker.py`: test-only provider and worker wiring.

### Task 1: Read existing access selections

**Files:** Create `backend/app/documents/access.py`, `backend/tests/api/test_document_access.py`; modify document schemas/API, tenancy service/API, group tests, OpenAPI and generated client.

**Interfaces:**
- Produce `GET /api/v1/documents/{document_id}/access` with `AccessResponse(visibility, user_ids, group_ids)`. UUID lists are sorted, distinct, and reflect stored grants.
- Produce `GET /api/v1/organizations/{organization_id}/groups/{group_id}/members?limit=50&offset=0` with the existing `MemberList` shape.
- Require document-manager and group-manager permissions respectively. Scope every read to the principal's organization; return 404 for a foreign or deleted resource.
- Produce `read_document_access(principal: RequestPrincipal, document_id: UUID, *, session: Session) -> AccessResponse`.
- Produce `list_group_members(principal: RequestPrincipal, group_id: UUID, *, session: Session, limit: int = 50, offset: int = 0) -> list[tuple[Membership, User]]`.

- [ ] Write `test_manager_reads_existing_grants_without_document_read_access`, `test_member_cannot_read_grant_targets`, `test_foreign_and_deleted_document_access_is_not_found`, and group membership pagination/tenant tests. Assert:
  ```python
  assert allowed.status_code == 200
  assert allowed.json()["user_ids"] == [str(expected_user_id)]
  assert member.status_code == 403
  assert foreign.status_code == 404
  ```
- [ ] Run `uv run pytest tests/api/test_document_access.py tests/api/test_groups.py -q` from backend. Expected: the new endpoint tests fail with 404 before implementation.
- [ ] Implement the scoped reads without changing write semantics or exposing document bodies. Do not add a migration.
- [ ] Export OpenAPI with `uv run python -m app.export_openapi`; regenerate the frontend client with `npm.cmd run generate:api`.
- [ ] Rerun the named tests and `tests/api/test_openapi_snapshot.py`. Expected: all pass.
- [ ] Commit: `add access editor read contracts`.

### Task 2: Scope-safe browser transport and shell

**Files:** Modify API client, auth provider, shell, main entry and router; create typed API adapters, WorkspaceBoundary, `frontend/tests/workspace-scope.spec.ts`, and reusable browser fixtures under `frontend/tests/helpers/`.

**Interfaces:**
- Produce `request<T>(path: string, init?: RequestInit, accessToken?: string): Promise<T>`; preserve an explicitly supplied content type, handle 204, and map non-JSON dependency failures to a safe error.
- Produce `documentsApi`, `tenancyApi`, and `chatApi` adapters using generated schema types. Every method takes `token: string` first and `signal?: AbortSignal` last.
- documentsApi methods: `list(limit: number, offset: number) -> Promise<DocumentResponse[]>`, `get(documentId: string) -> Promise<DocumentResponse>`, `access(documentId: string) -> Promise<AccessResponse>`, `upload(file: File) -> Promise<DocumentResponse>`, `versions(documentId: string) -> Promise<VersionResponse[]>`, `replace(documentId: string, file: File) -> Promise<DocumentResponse>`, `retry(documentId: string) -> Promise<DocumentResponse>`, `saveAccess(documentId: string, command: AccessCommand) -> Promise<DocumentResponse>`, and `remove(documentId: string) -> Promise<void>`. These parameter lists omit only the common token/signal parameters.
- tenancyApi methods: `members(organizationId, limit, offset) -> Promise<MemberList>`, `groups(organizationId, limit, offset) -> Promise<GroupList>`, `groupMembers(organizationId, groupId, limit, offset) -> Promise<MemberList>`, `setRole(organizationId, membershipId, role) -> Promise<MemberSummary>`, `setActive(organizationId, membershipId, isActive) -> Promise<MemberSummary>`, `createGroup(organizationId, name) -> Promise<GroupSummary>`, `renameGroup(organizationId, groupId, name) -> Promise<GroupSummary>`, `removeGroup(organizationId, groupId) -> Promise<void>`, `addGroupMember(organizationId, groupId, membershipId) -> Promise<void>`, and `removeGroupMember(organizationId, groupId, membershipId) -> Promise<void>`. IDs/names are strings, limit/offset are numbers, isActive is boolean, and role uses the generated Role type.
- chatApi methods: `query(command: QueryCommand) -> Promise<AnswerResponse>` and `trace(queryRunId: string) -> Promise<TraceResponse>`. DTO aliases come from the generated schema, not handwritten copies.
- Expose a monotonically increasing `workspaceRevision: number` and `expireSession(): void` from AuthProvider.
- Produce `WorkspaceBoundary({ children }: { children: ReactNode })`, mounted for one organization and revision, with its own QueryClient.
- Clear private UI immediately when logout or organization switching begins. Cancel pending requests on unmount. A failed switch ends in a recoverable login state, not stale content. Ignore results from obsolete auth transitions.
- A failed server logout must not be reported as successful revocation: keep private views hidden, report the failure, and offer retry. The browser cannot clear an HTTP-only refresh cookie itself.

- [ ] Add delayed-response, logout-during-request, session-expiry, malformed-error-body, and raw-upload-header tests. Assert:
  ```ts
  await expect(page.getByText("Old workspace secret")).toHaveCount(0)
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible()
  expect(uploadRequest.headers()["content-type"]).toBe("application/octet-stream")
  ```
- [ ] Run `npm.cmd run test -- workspace-scope.spec.ts`. Expected: new scope/reset behavior fails.
- [ ] Implement transport and scope cleanup. Do not put the token in cache keys. Key cached resources by organization/revision and resource identifiers.
- [ ] Enable Chat and Documents navigation for authenticated roles, and People/Groups only for owner/admin. Preserve backend enforcement for manually entered routes.
- [ ] Rerun scope tests and existing auth/tenant-navigation tests; run `npm.cmd run check`. Expected: all pass.
- [ ] Commit: `isolate browser state by workspace`.

### Task 3: People and group management

**Files:** Create `features/admin/PeoplePage.tsx`, `GroupsPage.tsx`, `GroupMembersEditor.tsx`, and `frontend/tests/administration.spec.ts`; modify router and styles.

**Interfaces:** Consume tenancyApi and WorkspaceBoundary. Use membership IDs for role/active/group-membership mutations; use user IDs only for document user grants. No user-creation or invitation endpoint is added.

- [ ] Add tests for member pagination, role changes, activation, last-owner conflict, group create/rename/delete, and group-member add/remove. Assert:
  ```ts
  await expect(page.getByRole("alert")).toContainText("last active owner")
  await expect(page.getByRole("checkbox", { name: "member@northstar.example" })).toBeChecked()
  await expect(page.getByRole("navigation")).not.toContainText("People")
  ```
- [ ] Run `npm.cmd run test -- administration.spec.ts`. Expected: missing screens/actions fail.
- [ ] Implement routes under `/organizations/$organizationId/members` and `/organizations/$organizationId/groups`. Use pages of 50, explicit loading/empty/unavailable states, and mutation-specific pending state.
- [ ] Confirm destructive actions, preserve forms after failures, and refresh current membership after changes affecting the signed-in user.
- [ ] Rerun browser tests and frontend checks. Expected: all pass.
- [ ] Commit: `add people and group management`.

### Task 4: Document upload and lifecycle

**Files:** Create DocumentsPage, UploadDocumentForm, DocumentDetailPage, DocumentStatus, and `frontend/tests/documents.spec.ts`; modify router/styles.

**Interfaces:** Consume documentsApi. Use `/documents` and `/documents/$documentId`. Upload File bytes, not JSON or multipart, with the filename encoded using URLSearchParams.

- [ ] Add tests for upload, duplicate upload, processing/ready state, unsupported file, oversized file, version replacement, failed retry, pagination, and deletion. Assert:
  ```ts
  await expect(page.getByRole("status")).toContainText("Queued")
  await expect(page.getByRole("button", { name: "Retry indexing" })).toBeVisible()
  await expect(page.getByText("<img src=x onerror=alert(1)>.txt")).toBeVisible()
  ```
- [ ] Run `npm.cmd run test -- documents.spec.ts`. Expected: missing lifecycle UI fails.
- [ ] Implement a 50-item list with pagination, bounded polling, version history, and explicit delete confirmation. Restrict management actions to owner/admin.
- [ ] Upload starts restricted by default. A failed subsequent grant save leaves the document restricted and clearly reports the unsaved access change.
- [ ] Display ingestion failures generically unless the API supplies a specific code; do not invent an extraction diagnosis. Explain image-only PDFs/OCR limits.
- [ ] Render filenames as text and stop polling on unmount, logout, and workspace switch.
- [ ] Rerun browser tests and frontend checks. Expected: all pass.
- [ ] Commit: `add document upload and lifecycle screens`.

### Task 5: Explicit document grant editing

**Files:** Create `features/documents/DocumentAccessEditor.tsx` and `frontend/tests/document-access.spec.ts`; modify upload/detail integration.

**Interfaces:** Consume Task 1 AccessResponse, tenancyApi, and documentsApi. Submit the existing AccessCommand via PATCH; preserve loaded IDs not visible on the current picker page.

- [ ] Add tests for organization-wide visibility, restricted direct/group grants, reopening existing selections, grants outside the first 50 picker results, cancellation, and backend projection failure. Assert:
  ```ts
  expect(savedCommand.user_ids).toContain(existingUnlistedUserId)
  await expect(page.getByRole("alert")).toContainText("Access was not saved")
  await expect(page.getByText("Access saved")).toHaveCount(0)
  ```
- [ ] Run `npm.cmd run test -- document-access.spec.ts`. Expected: missing editor/preservation behavior fails.
- [ ] Load current grants before enabling save. Default new uploads to restricted. Explain that admin role alone does not grant document reading.
- [ ] Use labeled checkboxes and explicit removal for existing targets; do not silently drop inactive/unlisted selections. Organization-wide mode intentionally clears explicit targets.
- [ ] Save only after the backend succeeds. On failure, keep the unsaved draft visibly separate from persisted access and offer retry.
- [ ] Rerun grant tests and frontend checks. Expected: all pass.
- [ ] Commit: `add explicit document access editing`.

### Task 6: Complete answers, citations, and safe traces

**Files:** Create ChatPage, AnswerPanel, QueryTracePanel, and `frontend/tests/chat.spec.ts`; modify router/styles.

**Interfaces:** Consume `chatApi.query(token, command, signal)` and `chatApi.trace(token, queryRunId, signal)`, typed from AnswerResponse/TraceResponse. Read a problem extension's query_run_id only after validating it is a UUID string.

- [ ] Add answered, abstained, failed, timeout, unconfigured-provider, stale-response, hostile-answer-text, and unauthorized-trace tests. Assert:
  ```ts
  await expect(page.getByText("No supporting evidence was found")).toBeVisible()
  await expect(page.getByRole("button", { name: "View query trace" })).toBeEnabled()
  await expect(page.locator("script[data-answer]")).toHaveCount(0)
  ```
- [ ] Run `npm.cmd run test -- chat.spec.ts`. Expected: missing chat/result states fail.
- [ ] Implement a labeled 4000-character question form with limit 10, loading/cancel/retry states, plain-text answers, and filename/location citations.
- [ ] Show only redacted stage decisions, timings, and allowed identifiers returned by the API. Do not invent accuracy scores, streaming, or strategy metadata absent from the API.
- [ ] Preserve trace access after a generation failure. Missing evidence must not imply that a forbidden document exists.
- [ ] Explain that a server-side LLM endpoint/model must be configured when generation is unavailable. Never request a provider key in the browser.
- [ ] Rerun chat tests and frontend checks. Expected: all pass.
- [ ] Commit: `add cited chat and query trace screens`.

### Task 7: Real-service browser journeys and handoff

**Files:** Create `backend/tests/e2e_app.py`, `e2e_worker.py`, `frontend/tests/product-journey.spec.ts`; modify e2e seeding, start-api script, verification script/CI, and README.

**Interfaces:**
- `tests.e2e_app:create_app() -> FastAPI` wraps the real application with deterministic test-only embedding/generation providers.
- `tests.e2e_worker` calls production run_once for seeded organizations against real PostgreSQL/Qdrant, using the same deterministic embeddings.
- Guard fixture setup with environment `test`, database `ragelit_e2e`, and dedicated collection `ragelit_e2e`. Never reset a collection named by arbitrary production configuration.
- Test server and worker share a per-run temporary data directory; terminate both child processes on test shutdown. Production code gains no fake-provider switch.

- [ ] Write a real owner setup/group grant/upload/ready/member chat journey, then revoke access and assert abstention. Add a second-group member and a second-tenant negative control. No route mocking is allowed for these core journeys.
- [ ] Assert cited filenames/locations match the permitted document; unauthorized chunk identifiers appear in no trace/citation. A same-role positive control must still receive evidence.
- [ ] Run `npm.cmd run test -- product-journey.spec.ts`. Expected: unconfigured fixture providers/worker prevent the new journeys from completing before wiring.
- [ ] Implement test-only wiring and synthetic accounts, then rerun. Expected: journeys pass against real stores without a paid model call.
- [ ] Update verification to run all Playwright tests, not the current two-file smoke subset. Keep generated API drift checks and frozen dependency installs.
- [ ] Run the full verification script. Expected: exit 0 with nonzero backend/browser counts, lint/types, generated API checks, and production build.
- [ ] Run secret scanning; perform post-code Playbook review through checklist-router; fix findings with regressions and make one independent whole-branch review using the chosen execution workflow.
- [ ] Write setup/demo/limits notes that distinguish deterministic browser providers from a real configured model. Record commands, counts, and exit codes; do not claim measured answer quality.
- [ ] Commit: `verify the document assistant portal`.

## Completion gate

An owner can manage groups, upload a supported document, load and save explicit
grants, replace/retry/delete a document, and inspect its indexing state. A member
can ask a cited question over granted active evidence, see an abstention after
revocation, and inspect only their own safe trace. Organization switching and
logout remove old private results, including delayed responses.

This completes the portal slice only. Audit automation, empirical evaluation,
live streaming, and v0.1 release hardening remain separate work.
