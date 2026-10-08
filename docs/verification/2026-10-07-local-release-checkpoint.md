# Local release checkpoint

Initial checks ran on 6 October UTC (7 October in Pakistan), with updates through
9 October Pakistan time. This is progress evidence,
not a completed v0.1 release. The tested application MVP is merged into public
`main`; the PostgreSQL zlib candidate remains a local, undeployed branch.

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
This does not prove arbitrary files safe.

## Dependency audit

The pinned OSV-Scanner 2.6.0 Windows binary matched its published SHA-256. The
repository's dependency-audit tests ran 15 tests with 10 platform cases skipped.
The first offline scan found `source-map-js` 1.2.1 in the frontend lockfile;
the lockfile now uses the registry's 1.2.2 release, which fixes the advisory.
The rerun scanned 96 Python and 170 npm packages and reported no issues. A
fresh temporary frontend install, Biome and TypeScript check, and production
build also exited 0.

`docker compose config --quiet` exited 0 and resolved the pinned PostgreSQL
18-alpine and Qdrant 1.15.4 images. The local Docker daemon was unavailable,
so no image scan or service-backed container check was claimed at that point.

Docker Scout 1.24.0 later scanned the local pinned images. The PostgreSQL image
digest `77f585114c32` contained 2 critical and 23 high findings across 3
packages. The Qdrant image digest `6ac4807063bb` contained 12 critical and 110
high findings across 40 packages. Scout recommends a newer Debian base for the
Qdrant image, but that requires a compatible Qdrant image update and a fresh
service-backed gate. No image pin was changed from this scan alone.

Candidate registry scans did not produce a clean replacement: PostgreSQL
`18.6-alpine3.24` still reported 2 critical and 23 high findings, while Qdrant
`v1.19.1` reported 7 critical and 26 high findings. At that point, the compose
pins remained unchanged pending a tested image/client update.

The [8 October container triage](../security/2026-10-08-container-triage.md)
separates startup-helper and build-inventory findings from linked runtime
libraries. It records advisory prerequisites and a filtered candidate scan;
it does not clear the images. The later defaults update is recorded below.

## Current local gate

The first full verification attempt reached the database integration stage but
the freshly created PostgreSQL service did not contain the test role expected by
the suite. After adding that role inside RAGelit's own container, the targeted
integration, API and migration command completed with 251 passed and 4 skipped
in 2:13:45. This was an environment correction; no application code changed.
The earlier backend unit stage completed with 782 passed and 3 skipped in 385.18
seconds.

The audit export produced by the same verification run passed the native report
validator with exit 0. The clean browser clone then ran the complete Playwright
journey: 57 passed in 10.8 minutes with one worker. That run covered sign-in,
tenant switching, document grants and revocation, hostile names and model text,
retrieval citations, audit jobs, and the real-store product journey. It used
fixture generation paths and did not load a language model.

A fresh deterministic injection release export then completed in 111.34 seconds.
The resistant, obeying and deny-all profiles all produced their expected exit
codes, and the saved three-report directory passed the native validator with
exit 0. This verifies the harness and artifact integrity only; it is not a
real-model injection-resistance result.

The three deterministic isolation strategies also completed in 1251.09 seconds.
All strategy assertions and the temporary release-directory checks passed. The
test process then stopped at its final destination assertion because this run
had pre-created the configured export directory. The six generated reports and
receipts were recovered into a fresh destination and passed the native isolation
validator with exit 0. This is deterministic harness evidence, not a
real-model quality or security result.

A fresh access-control demo also completed the safe, vulnerable-lab and
deny-all profiles over 51 cases each. The run IDs were
`8ef5b8fa-5493-45ba-8cce-5e75af56afdf` (safe),
`623d1b35-0ea8-4b7a-864a-a435802471d7` (vulnerable) and
`c3d1deb7-2bbe-471c-a0da-3920fa5d579b` (deny-all); their expected exits were
0/1/1. The six saved artifacts passed the native validator with exit 0. This is
the local vulnerable-to-fixed demonstration described in the operations guide.

These results are separate from a single end-to-end `scripts/verify.ps1` pass:
the first pass stopped at the missing PostgreSQL role, and rerunning it would
repeat the two-hour integration stage unnecessarily. The targeted rerun is the
authoritative current evidence for that stage.

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

## Linux CI portability fix (8 October)

Hosted run `37677735437` at `af4de04` stopped during backend type checking:
`ctypes.windll` was unavailable in the Linux type definitions. The benchmark
machine-metadata code now uses `sys.platform == "win32"` to select the Windows
branch, which mypy can narrow. No generation or retrieval behavior changed.

The Linux-targeted mypy check reproduced the error before the fix. Afterward,
Linux and Windows checks of `app/evaluation/cli.py` both passed. Its 20 CLI unit
tests passed in 25.67 seconds, and Ruff lint/format checks passed. These are
focused local checks, not a passing full hosted Linux gate.

The fix was pushed as `37d9ffc`. Hosted run `37795692591` passed all 235
backend type checks and the security job, then stopped at a Windows-only
subprocess flag in a script test helper. That helper now selects
`CREATE_NO_WINDOW` inside an explicit `sys.platform` Windows branch. Linux and
Windows script type checks pass; all five native Windows script regressions
passed in 9.634 seconds. Script lint, format and compilation checks passed.
The script fix was pushed as `f8e66bb`. The complete
[hosted Linux run](https://github.com/HuzaifaAbdulRehman/ragelit/actions/runs/37796567426)
at `f8e66bb0c348210482a5fa5d044f7af183361852` passed both jobs. Verification
took 22 minutes 34 seconds; the security job took 29 seconds.

| Hosted check | Observed result |
|---|---|
| Backend and verification-script mypy | 235 plus 3 files; exit 0 |
| Backend unit tests | 785 passed in 100.91 seconds |
| Integration, API and migration tests | 251 passed, 4 skipped in 1024.96 seconds |
| Frontend static checks and production build | Exit 0 |
| Generated API contract | No drift |
| Browser journeys | 57 passed in 2 minutes |
| Access, injection and isolation release exports | Native validators passed; all three uploaded |
| Secret and locked-dependency checks | Security job passed |

All three export sets were downloaded from this exact run into a fresh ignored
directory and replayed through their native validators on Windows. Each exited
0. These are deterministic fixture results, not real-model measurements. This
hosted pass replaces the earlier pending Linux CI status; it does not clear the
separate container findings or generation benchmark.

## Qdrant candidate compatibility (8 October)

A separate loopback-only Qdrant 1.19.2 container and disposable PostgreSQL
container ran the native safe access-control audit at clean source `a4d6f91`.
All 51 cases completed with complete coverage and exit 0. The saved report and
receipt passed the original reader. The exact image digest, run ID and remaining
scan findings are recorded in the [container triage](../security/2026-10-08-container-triage.md).
Both owned containers were removed afterward. At that stage, existing services,
their data and compose pins were unchanged. This is safe-profile compatibility evidence,
not a full candidate-image release gate.

## Fresh-data Qdrant defaults (8 October)

Commit `44a70aa31598c2d69094f31fae9d8fa3b225db76` updates Compose and CI to
the exact Qdrant 1.19.2 image recorded in the triage, with Python client 1.19.1.
Only the client changed in the Python lockfile. Regression checks verify the
resolved Compose image and loopback ports, CI's matching image and the installed
client's major/minor version.

Both new version assertions were checked through a failing-to-passing cycle.
The focused contract/retrieval command passed all 15 tests in 7.27 seconds.
Ruff lint/format, compilation and Linux-targeted mypy across 238 files passed.
Python editor diagnostics were unavailable because no Python LSP is configured.
The staged redacted secret scan found no leaks. An independent read-only review
found no actionable defects in this bounded update.

The [updated hosted gate](https://github.com/HuzaifaAbdulRehman/ragelit/actions/runs/37809980310)
passed at that exact source revision. Verification took 23 minutes 23 seconds;
the security job took 33 seconds.

| Updated hosted check | Observed result |
|---|---|
| Backend and script mypy | 235 plus 3 files; exit 0 |
| Backend unit tests | 786 passed in 107.19 seconds |
| Integration, API and migration tests | 251 passed, 4 skipped in 1066.92 seconds |
| Frontend checks, generated API contract and production build | Exit 0; no contract drift |
| Browser journeys | 57 passed in 2 minutes |
| Access, injection and isolation exports | Native validators passed; all three uploaded |
| Secret and locked-dependency checks | Security job passed |

The three original export sets were downloaded from this updated run into fresh
ignored storage at `data/ci-artifacts/run-37809980310`. Each contains three
reports and three receipts. The access-control, injection and isolation readers
all validated their sets on Windows with exit 0 using the frozen client 1.19.1
environment. These checks loaded no model and changed no application data.

Existing local services and volumes were not restarted or migrated. The local
Qdrant service remains on 1.15.4; these checks do not establish its compatibility
with the updated client. The [operator warning](../../OPERATIONS.md#existing-qdrant-data)
requires sequential minor upgrades and a restorable backup before using newer
images with existing data. A fresh-data CI pass is not a migration test or
container-security clearance.

## Review and remaining work

The container triage now records advisory-level follow-up for the exact images.
Offline, read-only metadata inspection confirmed the Qdrant UI inventory and
library versions, distinguished Node execution from browser assets, and found
Alpine's zlib 1.3.2-r1 remediation target for PostgreSQL. No image was rebuilt or
service changed. Unverified runtime paths and the container release gate remain
open; the follow-up is not a risk-acceptance decision.

One independent read-only review covered `58e7a6f..8b7d3aa`, including asset
verification, workspace identity, report replay, provenance, access boundaries
and audit jobs. It found no new implementation defect requiring a fix. Earlier
focused test logs were inspected, not treated as a current-head full-suite pass.

Still required: remediation or justified, scoped disposition of the container
findings and release sign-off. The matching Qdrant image/client
pair now passes the full fresh-data hosted gate at `44a70aa`; later
documentation-only changes do not alter that tested code.
Real-model generation/security quality and revocation measurements need
suitable hardware. The
current evidence is a well-tested local MVP, not a completed v0.1 release claim.

Original reports, receipts, loaded-model proofs, logs and process leases remain
in ignored local storage under
`.superpowers/sdd/2026-10-06-real-model-benchmark/`. They are not committed public
artifacts. The ledger records the clean-clone path and exact report locations.

## Merged MVP and PostgreSQL follow-up (9 October)

[PR 27](https://github.com/HuzaifaAbdulRehman/ragelit/pull/27) merged the tested
MVP at `e6465240f9a107907b13930adfd125248876d74b`. Both the PR gate and
[main CI](https://github.com/HuzaifaAbdulRehman/ragelit/actions/runs/37821775490)
passed. No unchanged local suite was rerun for this checkpoint.

The [PostgreSQL zlib candidate](../security/2026-10-09-postgres-zlib-candidate.md)
changes only zlib to 1.3.2-r1 and passes three opt-in Docker checks. It is not
selected by Compose or CI and has not changed an existing service or volume.
Its exact-image critical/high scan reports 2 critical and 22 high findings;
the zlib advisory is absent, but gosu and libxml2 findings remain.
Container-security disposition, real-model generation/security and revocation
measurements, and v0.1 sign-off remain open.

## Hardening coverage audit (9 October)

The inspected assertions and verification script match the passing main revision
`e6465240f9a107907b13930adfd125248876d74b`. Its CI run still reports completed
success. No unchanged suite was rerun for this audit.

| Requirement | Existing test evidence | Limit |
| --- | --- | --- |
| Malformed uploads | `test_malformed_docx_is_rejected` in `backend/tests/unit/documents/test_processing.py`; invalid-content ingestion asserts zero active points. | Synthetic malformed inputs, not a parser fuzzing campaign. |
| Oversized requests | `test_invalid_upload_has_no_stored_file` in `backend/tests/api/test_documents.py` checks HTTP 413 and removal of partial uploads. | Configured document-upload limit, not every HTTP body type. |
| Concurrency | `test_concurrent_owner_changes_keep_one_active_owner` in `backend/tests/api/test_members.py`; two audit workers cannot execute concurrently in `backend/tests/integration/audit_jobs/test_claims.py`. | Specific ownership and worker-lock races, not load testing. |
| Recovery | Expired audit leases require explicit confirmation; superseded ingestion claims cannot activate points. The POSIX worker test sends SIGTERM and checks child reaping. | Lease and shutdown tests do not prove a full process restart-and-recovery exercise. |
| Provider timeouts | `test_transport_timeout_has_stable_timeout_code` in `backend/tests/unit/chat/test_provider.py` checks HTTP 504, the stable error code and redaction of transport details. | Injected transport timeout, not successful real-model generation. |

The combined hardening checkbox stays open for the unverified restart exercise.
The PostgreSQL candidate still needs application compatibility evidence; the
passing main gate used the existing database image. Container disposition and
the full real-model measurement matrix remain separate release requirements.
