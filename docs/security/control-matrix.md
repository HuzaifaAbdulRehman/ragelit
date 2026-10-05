# Security control matrix

This maps the development build's controls to enforcement code and regressions.
The control IDs are local to RAGelit, not OWASP or ASVS requirement IDs.
See the [threat model](../superpowers/specs/2026-09-29-ragelit-security-design.md#threat-model)
for the attacker assumptions. This is not a certification or a claim that
customer documents have been audited.

Linked tests describe the expected behavior. Recorded results apply to their
listed revisions; they do not establish a current-head release pass.

| ID and boundary | Enforced behavior | Code | Regression |
| --- | --- | --- | --- |
| C01. Identity | Verify signed access claims and expiry, then reload active user, membership, role and session. Refresh rotates tokens; logout and replay revoke sessions. | [Session scope](../../backend/app/tenancy/scope.py), [session service](../../backend/app/identity/service.py) | [Login, refresh and logout](../../backend/tests/api/test_auth.py), [stored-token hashes and replay](../../backend/tests/integration/identity/test_sessions.py) |
| C02. Roles versus grants | Server-side action guards control management and auditing. Owner/admin role alone does not allow reading restricted documents in chat. | [Action policy](../../backend/app/tenancy/policy.py), [document eligibility](../../backend/app/retrieval/authorization.py) | [Owner-role and forged-group denial](../../backend/tests/api/test_retrieval.py), [audit role changes](../../backend/tests/api/test_audits.py) |
| C03. Relational tenant boundary | Run ordinary requests as a non-owner database role. Force RLS on tenant tables and set transaction-local identity context from the verified principal. | [Tenant policies](../../backend/app/alembic/versions/0002_tenant_rls.py), [document policies](../../backend/app/alembic/versions/0003_documents.py), [request context](../../backend/app/tenancy/rls.py) | [Non-owner privileges, absent context and pool isolation](../../backend/tests/integration/tenancy/test_rls.py) |
| C04. Retrieval boundary | Refresh membership/groups and eligible current versions from SQL. Apply organization, active-state, version and grant filters to both prefetch branches and fusion. Dependency failure has no unfiltered fallback. | [Authorization](../../backend/app/retrieval/authorization.py), [shared store](../../backend/app/retrieval/store.py) | [Real hybrid retrieval, stale grants and foreign tenants](../../backend/tests/api/test_retrieval.py), [store query contract](../../backend/tests/unit/retrieval/test_store_search.py) |
| C05. Lifecycle | Scope mutations by tenant and document. Restrict grants to same-tenant identities, serialize state changes, and exclude revoked, deleted or superseded versions even with stale vector payloads. Deletion is soft, not erasure. | [Document lifecycle](../../backend/app/documents/lifecycle.py), [SQL eligibility](../../backend/app/retrieval/authorization.py) | [Stale projections and lifecycle denial](../../backend/tests/api/test_retrieval.py), [foreign-document metadata](../../backend/tests/api/test_documents.py) |
| C06. Context and citations | Build bounded context only from AuthorizedChunk values. Abstain without evidence. Reject citation IDs outside the supplied context and nonempty answers without citations. This validates citation membership, not factual correctness. | [Context builder](../../backend/app/chat/context.py), [chat service](../../backend/app/chat/service.py) | [Type and context limits](../../backend/tests/unit/chat/test_context.py), [abstention, citation failure and traces](../../backend/tests/api/test_chat.py) |
| C07. Trace privacy | Scope query-run reads to the caller and organization. Persist opaque chunk IDs, decisions and timings, not source bodies, questions or generated answers. Candidate and delivered evidence remain distinct in audits. | [Trace service](../../backend/app/chat/service.py), [audit observer](../../backend/app/audits/observer.py) | [Actor-private traces](../../backend/tests/api/test_chat.py), [stage observations without source text](../../backend/tests/api/test_audit_observation.py) |
| C08. Bounded inputs/providers | Bound uploads and extraction; use UUID-based storage keys. PDF parsing has a child-process memory limit and timeout. Provider requests have a total deadline, bounded responses and no redirect forwarding. | [Upload storage](../../backend/app/documents/storage.py), [extraction](../../backend/app/documents/extraction.py), [provider](../../backend/app/chat/provider.py) | [Invalid/oversized uploads](../../backend/tests/api/test_documents.py), [processing limits](../../backend/tests/unit/documents/test_processing.py), [timeouts, redirects and malformed output](../../backend/tests/unit/chat/test_provider.py) |
| C09. Owned audit/lab resources | Audit bootstrap accepts only explicit local/test loopback targets and owned namespaces. Validate markers, saved bindings, role privileges and resource inventory; refuse adoption or reset. Broken retrieval requires explicit lab opt-in. | [Workspace ownership](../../backend/app/audits/workspace.py), [guarded post-filter](../../backend/app/audits/isolation_lab.py) | [Workspace drift](../../backend/tests/integration/audits/test_isolation_workspace.py), [foreign-store and opt-in rejection](../../backend/tests/integration/audits/test_isolation_targets.py) |
| C10. Audit jobs | Require current privileged role and tenant-scoped run lookup. The browser cannot supply execution settings. One outstanding job per organization; expired leases require explicit operator recovery, not an automatic passing rerun. | [Job API](../../backend/app/audit_jobs/api.py), [job service](../../backend/app/audit_jobs/service.py) | [Role/tenant/lease checks](../../backend/tests/api/test_audits.py), [recovery and owned-child termination](../../backend/tests/unit/audit_jobs/test_worker.py) |
| C11. Audit decisions | Require the exact case inventory and required stage evidence. Missing/truncated observations stay inconclusive; deny-all fails positive controls. Retain first exposure even if a later stage contains it. | [Scorer](../../backend/app/audits/scoring.py), [report gate](../../backend/app/audits/reports.py) | [Coverage, deny-all and first exposure](../../backend/tests/unit/audits/test_scoring.py), [runtime failure and no-overwrite](../../backend/tests/unit/audits/test_reports.py) |
| C12. Instruction attacks | Treat document evidence as untrusted data, not access authority. The separate injection CLI measures authorized payload exposure and candidate/delivered output signals, with benign utility controls. A system instruction is not a universal injection defense. | [Generation prompt](../../backend/app/chat/provider.py), [injection scorer](../../backend/app/audits/injection_scoring.py) | [Eligible attacks, abstentions and explicit denominators](../../backend/tests/unit/audits/test_injection_scoring.py), [real deterministic CLI profiles](../../backend/tests/integration/audits/test_injection_cli.py) |
| C13. Report bytes and rendering | Keep bounded, redacted original JSON with SHA-256 receipts and refuse overwrite. Injection/isolation readers reject duplicate original fields and replay scoring. Escape every HTML value; exports have no scripts or remote assets. | [Access artifact reader](../../backend/app/audits/artifacts.py), [injection reader](../../backend/app/audits/injection_reports.py), [isolation reader](../../backend/app/audits/isolation_reports.py), [HTML renderer](../../backend/app/audits/html.py) | [Duplicate/tampered injection bytes](../../backend/tests/unit/audits/test_injection_reports.py), [isolation score replay](../../backend/tests/unit/audits/test_isolation_reports.py), [hostile HTML](../../backend/tests/unit/audits/test_html.py) |

## Recorded evidence and open gates

- [Authorized RAG](../verification/2026-10-02-authorized-rag.md) records
  real PostgreSQL/Qdrant controls and their review fixes.
- [Portal audits](../verification/2026-10-04-audit-dashboard.md) record
  Windows and Linux portal-to-worker safe-pack journeys.
- [HTML exports](../verification/2026-10-05-html-audit-export.md) record
  escaped offline rendering and protected downloads.
- [Injection pack](../verification/2026-10-05-injection-pack.md) records
  deterministic resistant, obeying and deny-all profiles. No real-model
  resistance result is available.
- [Isolation comparison](../verification/2026-10-05-isolation-comparison.md)
  records the three real 51-case target journeys. The successful CLI export
  cohort, final branch review and current-head Linux gate remain pending.
  The sleep-interrupted CLI report stays inconclusive.

Passing a single-report integrity check does not change its recorded audit
outcome. A checksum is not a signature or proof against an operator fabricating
identities or observations. The access-control reader checks inventory/outcome
consistency; injection and isolation readers also replay their saved
case evidence. Retrieval observations cover fused results, not internal prefetch
candidates.

## Release work still required

The existing roadmap still requires pinned real-model evaluation and per-query
artifacts: unauthorized retrieval, context exposure, output disclosure, injection
success, Recall@10, MRR@10, citation correctness, p50/p95 latency, index time,
storage, collection count and revocation delay. Paired confidence intervals
must be reported where appropriate. Deterministic providers cannot supply
real-model quality or resistance claims.

Clean-clone Windows/Linux checks, the recorded demo and current-head release
gates are also pending. The [CI workflow](../../.github/workflows/ci.yml) defines
secret scanning and offline locked-dependency auditing; configured gates are
not evidence that the release passed them.

Give audit bootstrap credentials only to the operator worker; ordinary requests
need the non-owner application database URL. Use local services as documented.
TLS termination and deployment hardening are an operator responsibility.
Host, database-admin and malicious-provider compromise are outside the tested
threat model. This build does not certify arbitrary
company documents, erase soft-deleted data or prove universal prompt-injection
resistance.
