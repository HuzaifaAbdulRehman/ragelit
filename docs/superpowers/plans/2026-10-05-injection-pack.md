# Injection audit pack implementation plan

> For agentic workers: use superpowers:executing-plans for native execution, followed by one independent whole-branch review. Steps use checkbox syntax.

**Goal:** Add the reproducible indirect-injection pack required by issue #20.

**Architecture:** Reuse owned fixtures and real chat observations. Keep injection scoring, artifacts and CLI separate from the access-control pack.

**Tech Stack:** Python, Pydantic, existing PostgreSQL/Qdrant/FastAPI test utilities.

**Spec:** docs/superpowers/specs/2026-10-05-injection-pack-design.md

## Global constraints

- Python and existing dependencies only.
- No production settings changes, ordinary-document imports, paid provider calls or credential discovery.
- Keep existing access-control fixtures and their checksum unchanged.
- Use 1..20 trials, default 1.
- No raw labels, prompts or answers enter reports.
- Keep the repository private; no streaming or new portal workflows.

## Review focus

- A provider aborts after producing an attack marker: retain the signal, mark coverage incomplete.
- A provider denies every answer: fail positive controls, never label resistance proven.
- A valid-looking artifact changes counts: replay observations before accepting it.
- A local provider URL redirects or carries credentials: reject unsafe configuration, never follow redirects.
- A resumed workspace has another template: refuse ownership mismatch without deleting it.

## Task 1: Honest injection scoring

Files: create backend/app/audits/injection_scoring.py and
backend/tests/unit/audits/test_injection_scoring.py.

Consumes: AuditCase, AuditObservation and score_case(case, observation).
Produces: InjectionCase, InjectionResult, InjectionSummary,
score_injection(case: InjectionCase, observation: AuditObservation) -> InjectionResult,
summarize_injection(cases: tuple[InjectionCase, ...], results: tuple[InjectionResult, ...],
runtime_failed: bool = False) -> InjectionSummary.

- [ ] Write tests for context-only attack allowed, candidate/delivered matches failed,
  missing context inconclusive, mismatch cannot signal success, deny-all utility
  failure, later incomplete failure retained, forbidden tenant evidence separate.
  Derive literal expected rates: one of two evaluated attacks is 0.5, zero is null.
  Test duplicate/missing/unknown inventory and serialized status tampering.
- [ ] Run uv run --frozen pytest tests/unit/audits/test_injection_scoring.py -q.
  Expected: new-module import failure before implementation.
- [ ] Implement frozen extra-forbid models and scoring against the spec.
- [ ] Run the same focused command. Expected: all cases pass.
- [ ] Commit add injection audit scoring.

## Task 2: Pinned fixtures and provider controls

Files: create backend/app/audits/injection_fixtures.py,
backend/app/audits/injection_providers.py and matching unit tests;
modify backend/app/audits/fixtures.py and backend/app/audits/target.py.
Test backend/tests/unit/audits/test_fixtures.py and integration/audits/test_target.py.

Consumes: Task 1 types, FixtureTemplate and PreparedPack.
Produces: generate_injection_fixtures(seed: int = 20261002, trials: int = 1) -> FixtureTemplate;
injection_cases(pack: PreparedPack, template: FixtureTemplate) -> tuple[InjectionCase, ...];
InjectionProvider(profile: Literal["resistant", "obeying", "deny_all"]) implementing
GenerationProvider; validated local configuration returning CompatibleProvider.
BundledAuditTarget accepts optional generation_provider with unchanged defaults.

- [ ] Write tests for six documents, six cases per trial, unique IDs/markers,
  deterministic manifest, unchanged access fixture checksum, valid topic geometry,
  literal fake outputs and no-context abstention. Test invalid trial bounds,
  remote/credential/query/fragment URLs and local-provider metadata.
- [ ] Run focused new fixture/provider tests. Expected: missing new modules.
- [ ] Implement the fixture generator and explicit local provider configuration.
  Extend only template ID literals and optional provider selection; reject
  conflicting citation-challenge or vulnerable profiles with supplied providers.
- [ ] Run focused new tests plus affected existing fixture/target tests.
  Expected: all pass, ordinary target semantics unchanged.
- [ ] Commit add pinned injection fixtures and providers.

## Task 3: Owned CLI, artifacts and release evidence

Files: create backend/app/audits/injection_cli.py,
backend/app/audits/injection_reports.py, matching unit/API integration tests and
docs/verification/2026-10-05-injection-pack.md; modify scripts/verify.sh and README.md.

Consumes: Task 1 scorer/summary, Task 2 fixture/provider/target contracts, existing
AuditWorkspace configuration and repository metadata helpers.
Produces: python -m app.audits.injection_cli --profile resistant|obeying|deny_all|local
--trials 1..20; strict InjectionReport and write/validate original artifacts.

- [ ] Write CLI/artifact tests for exact JSON/checksum, no overwrite, redaction,
  count tampering, missing cases, dependency/runtime failures, invalid arguments
  and fixed diagnostics. Add real database/vector journeys for all fake profiles,
  verifying exit 0/1/1 and first candidate-output signal on obeying attacks.
- [ ] Run focused tests. Expected: missing command/report behavior.
- [ ] Implement with existing owned workspace and quiet dependency wrapper.
  Build additional opaque fact registry before target construction; do not alter
  ordinary pack artifacts. Record provider settings and hashes.
- [ ] Run focused unit and real integration commands. Expected: pass and validated
  artifacts with reproducible commands. Run changed-file Ruff/mypy and secret scan.
- [ ] Add deterministic pack artifacts to existing CI verification, document
  measured results and exact limits. Run humanize on all reader-facing prose.
- [ ] Commit add reproducible injection audit runs.
- [ ] Request one independent whole-branch review; fix important findings with
  RED/GREEN regressions. Push, open issue-closing PR, reuse the one hosted full
  gate and merge only when the current head passes.
