# Local release checkpoint

Checks ran on 6 October UTC (7 October in Pakistan). This is progress evidence,
not a completed v0.1 release. GitHub delivery remains on hold; repository
visibility was left unchanged at the user's request.

## Clean Windows setup

A new local Git clone pinned clean source
`8b7d3aa3fb8ec5fef85388cf946ab47aa578765e`. It contained no model weights,
environment files or previous audit workspace. Python 3.13.5, PostgreSQL 16 and
Qdrant 1.15.4 were used locally.

| Check | Observed result |
|---|---|
| Backend `uv sync --frozen` and native audit CLI help | Exit 0 |
| Frontend `npm ci`, `npm run check`, `npm run build` | Exit 0 |
| Generated API schema and tracked diff | Exit 0; no drift |
| Ruff format/lint and mypy across app, tests and scripts | 238 files; exit 0 |
| Native safe access-control audit | 51 cases; exit 0; complete coverage |
| Saved audit JSON and receipt validation | Exit 0 |
| Verification script unit tests | 15 run; 10 skipped; exit 0 |

The safe audit run is `1a2d8f6d-995a-4923-96b8-8d5233c89573`. Its metadata records
the clean source above and deterministic fixture providers. It tests access
boundaries, not real-model quality. The process-group test skipped on Windows;
no browser journey or full milestone-gate pass follows from these checks.

## Linux setup smoke and portal captures

A committed-source archive of `8b7d3aa` was installed in the already-cached
`ghcr.io/astral-sh/uv:python3.13-bookworm-slim` image using Python 3.13.11.
`uv sync --frozen`, native audit CLI help and benchmark CLI help all exited 0.
The owned container had a 512 MiB memory cap, one CPU and an eight-minute
timeout. It exited and was removed. This was a local setup smoke, not hosted
Linux CI or a service-backed Linux audit.

Four PNG screenshots under `docs/screenshots/` show the production frontend
build from the same source with synthetic E2E services. The document was
uploaded through the UI, indexed by the worker and given a persisted direct-user
and Engineering group grant. The chat screenshot shows the extractive fixture
answer and its actual citation. The audit screenshot shows a real queued job
with unknown outcome; no audit was run for that capture. These are interface
illustrations, not real-model results or replacement release artifacts.

The captures used a 1366 by 1000 viewport (full-page for document access).
They contain invented `.example` accounts and no passwords or provider keys.
The images were inspected before inclusion. Owned preview/API/worker/browser
processes were stopped afterward; temporary uploaded fixture files were removed
by the existing E2E cleanup. The dedicated test database retains only synthetic
records until the next fixture reset.

A local, redacted Gitleaks branch-history scan covered changes from `58e7a6f`
through `93acaab`: 63 commits scanned, about 1.25 MB, exit 0, no detected leaks.
The log and empty JSON report are retained in the local ledger directory.
This does not replace dependency/container scans or prove arbitrary files safe.

## Full real-model baseline

Run `8b247148-ce0f-4751-8e73-2264cd5fb582` used clean source `93cc651`, the pinned
[embeddings](../evaluation/model-pins.md), [generation baseline](../evaluation/generation-pins.md),
and all 87 queries in `natural-utility-v1`, seed `20261005`. The production
shared-collection pre-filter strategy was used. This is an authored synthetic
corpus, not a representative company dataset.

| Measurement | Result |
|---|---|
| Recall@10 | 1.0 |
| MRR@10 | 0.9396551724137931 |
| Accepted retrieval p50 / p95 | 74.3746 / 240.5254 ms |
| Summed ingestion-worker time | 127,911.5512 ms |
| PostgreSQL storage | 9,255,959 bytes |
| Uploaded source storage | 7,905 bytes |
| Allocated Qdrant collection storage | 5,505,024 bytes; one collection |
| Generation | 87 timeouts; answer/citation quality unknown |
| Revocation | 1 of 3 probes recorded; none measured; delay unknown |

The utility and cost artifacts both replayed successfully for integrity. Both
retain run exit 2 and incomplete coverage. Ingestion time excludes download and
model loading; it is not setup wall time. No paired cross-strategy interval,
real-model injection-resistance claim or passing benchmark follows from this run.

A separately pinned smaller CPU model also timed out on the fixed full-context
query. Its one-repetition diagnostic measured 41.4161 seconds for 2,129 prompt
tokens, before generation. That exceeds the unchanged 30-second request deadline
for this tested configuration. The user chose to stop model experiments and
finish the other local checks; the generation benchmark remains open.

## Review and remaining work

One independent read-only review covered `58e7a6f..8b7d3aa`, including asset
verification, workspace identity, report replay, provenance, access boundaries
and audit jobs. It found no new implementation defect requiring a fix. Earlier
focused test logs were inspected, not treated as a current-head full-suite pass.

Still required: the current-head full milestone gate, remaining security/container
scan evidence, Linux CI setup/demo, the vulnerable-to-fixed demo and final release
documentation. Real-model generation/security quality and revocation measurements
need suitable hardware. A local Linux setup smoke is separate from Linux CI.

Original reports, receipts, loaded-model proofs, logs and process leases remain
in ignored local storage under
`.superpowers/sdd/2026-10-06-real-model-benchmark/`. They are not committed public
artifacts. The ledger records the clean-clone path and exact report locations.
