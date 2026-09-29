# RAGelit reference review

Date: 2026-09-29

## Why this review exists

RAGelit should reuse proven infrastructure without becoming a renamed fork of
another RAG security project. This review fixes the sources, commits, licenses,
and reuse boundary before implementation begins.

The local clones live under `references/`. That directory is excluded through
`.git/info/exclude`; it must never be committed or shipped with RAGelit.

## Reviewed repositories

- [FastAPI full-stack template](https://github.com/fastapi/full-stack-fastapi-template),
  commit `cb740b656d7a0a6c5e12c7bf8e50343ec94ee9c7`, MIT. It provides
  FastAPI, PostgreSQL, Alembic, React, a generated API client, Playwright,
  Docker Compose, and authentication tests. Adapt the structure and selected
  commodity code, keep the MIT notice, and record copied files.
- [Pansoph](https://github.com/autoargos/pansoph), commit
  `073aae68440847c039bfeb89f431a9fec639d834`, Apache-2.0. It provides
  permission-aware retrieval, revocation tests, authorization ports, and
  fail-closed connector tests. Reuse test ideas and interface boundaries. Do
  not copy its overfetch-then-authorize pipeline because it does not meet
  RAGelit's pre-filter invariant.
- [RAGFence](https://github.com/eduardbar/RAGfence), commit
  `1f445ce6d47c99994cadf627c34b193ba4ad9b98`, Apache-2.0. It provides
  synthetic organizations, adapter contracts, deterministic security cases,
  JSON reports, and CI exit codes. Treat it as the closest comparison baseline
  and implement RAGelit's core independently.
- [RagenAI](https://github.com/webamigos/RagenAI), commit
  `8ac63ba505aba9f6c327062b4e9077cbd6e36a7f`, Apache-2.0. It provides
  full product workflows, Qdrant, tenant-scoped models, a security-event UI,
  and retrieval evaluations. Reuse product and test patterns only. Its
  documented tenant guard is warning-only, so it is not an enforcement model.
- [garak](https://github.com/NVIDIA/garak), commit
  `8d1259ef310e4803cf5a4cc77267fdfdc24434ec`, Apache-2.0. It separates
  probes, targets, detectors, attempts, and reports. Use that separation as an
  architectural reference and consider an adapter after RAGelit's audit packs
  work.
- [PyRIT](https://github.com/microsoft/PyRIT), commit
  `fe789b5b154275fd150e58ac326303f2dd235536`, MIT. It provides target
  adapters, scenario registries, attacks, scorers, persistent results, and web
  surfaces. Use its typed contracts as a reference. Do not import PyRIT into
  the MVP because its dependency and feature surface is much larger than
  RAGelit's two initial audit packs.

## Other implementations checked

The following projects were reviewed through their public repositories but were
not cloned:

- [AccessGuard RAG](https://github.com/Anupam2528/accessguard-rag) uses a
  microservice design with OpenSearch filters and redundant tenant checks. Its
  Java and AWS stack does not fit this project, but its negative integration
  tests are useful examples.
- [RAGGuard](https://github.com/maximus242/ragguard) turns authorization
  decisions into vector-database filters. It overlaps with RAGelit's retrieval
  boundary, but it is a library rather than an end-user application and audit
  lab.
- [PrivateGPT](https://github.com/zylon-ai/private-gpt) is a mature private
  document assistant. It is useful market evidence, but it does not supply the
  access-control audit contribution.
- [Promptfoo](https://github.com/promptfoo/promptfoo) provides broad red-team
  coverage. It is too broad to use as RAGelit's base and its repository is
  unnecessarily large for a local reference clone.

## Standards and official guidance

- [OWASP LLM08:2025](https://genai.owasp.org/llmrisk/llm082025-vector-and-embedding-weaknesses/)
  identifies unauthorized access, cross-context leakage, poisoning, and weak
  vector-store permissions as RAG risks.
- [OWASP LLM01:2025](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)
  states that RAG does not remove indirect prompt-injection risk.
- [NIST AI 600-1](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence)
  covers indirect injection, data poisoning, privacy, and system resilience.
- [Qdrant multitenancy guidance](https://qdrant.tech/documentation/tutorials/multiple-partitions/)
  recommends a shared collection with an indexed tenant payload for many small
  tenants and documents the trade-off of separate collections.
- [Qdrant filtering guidance](https://qdrant.tech/documentation/search/filtering/)
  documents compound filters and `match any`, which RAGelit needs for user and
  group grants.

## What RAGelit will reuse

### Directly adaptable under MIT

The FastAPI full-stack template is the only planned source of copied commodity
code. Candidate areas are Docker Compose, Alembic setup, password hashing,
token validation, generated OpenAPI client configuration, and Playwright test
setup. Every copied or substantially adapted file must be recorded in
`THIRD_PARTY_NOTICES.md` before its first commit.

RAGelit will target Python 3.12 rather than copying the template's current
Python 3.14 requirement. Email delivery, password reset, Traefik, Adminer, and
deployment-specific services will be removed unless a milestone needs them.

### Patterns to reimplement

- Pansoph: authorization ports, revocation scenarios, redundant tenant checks,
  and permission-focused integration tests.
- RAGFence: deterministic canaries, typed observations, target adapters, and
  report exit codes.
- RagenAI: role-specific product views, audit-event presentation, and tests that
  check tenant scoping at architectural boundaries.
- garak and PyRIT: separate a test case, target, observation, scorer, and report.

These sources may influence names and boundaries, but copied code requires a
fresh license review and a notice entry.

## What RAGelit will not reuse

- No repository will be forked as the RAGelit base.
- No code without a clear license will be copied.
- No secrets, real company documents, generated credentials, or private test
  data will enter the project.
- No claim from a README will be repeated as a RAGelit result without a local
  test or committed benchmark artifact.
- No client-provided tenant, role, or group value will become an authorization
  decision.
- No post-generation filter will be treated as an access-control boundary.

## RAGelit's distinct contribution

Existing projects cover parts of this space. RAGelit's contribution is the
combination below:

1. A usable multi-tenant document assistant with admin, member, and auditor
   workflows in one portal.
2. A server-derived access scope enforced inside every Qdrant search.
3. Stage-decomposed audit evidence showing whether a canary reached retrieval,
   prompt context, citations, or output.
4. A controlled experiment comparing an unsafe post-filter baseline, Qdrant
   payload pre-filtering, and stronger tenant separation.
5. Security and utility reported together: unauthorized retrieval, context
   exposure, output disclosure, allowed-query recall, and latency.

This is a portfolio and research testbed, not a security certification tool.
Passing its fixed audit packs proves only that the tested controls held for the
recorded configuration and dataset.

## License process

Before adapting third-party code:

1. Confirm the source repository and exact commit in this document.
2. Confirm the governing license for the file or directory.
3. Prefer a dependency or a clean reimplementation when attribution would be
   unclear.
4. Add the source, license, files, and nature of changes to
   `THIRD_PARTY_NOTICES.md`.
5. Preserve any required copyright and license notices.
6. Review the final public diff so the ignored `references/` directory and any
   external fixture data are absent.

Private development does not change these rules, but the record stays
lightweight: source, commit, license, copied files, and material changes. The
repository remains private unless the owner later decides otherwise.
