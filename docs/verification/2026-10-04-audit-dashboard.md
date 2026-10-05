# Audit dashboard verification

Recorded on 5 October 2026 in `feat/audit-engine`. This covers the
synthetic audit dashboard, not the whole research roadmap or a production
deployment. The real portal-to-worker journey passed on Windows and Linux.

## Source and checks

The final backend gate used `19df382`. Ruff formatting/lint and mypy
passed 157 files. Pytest passed 394 checks, skipped one POSIX-only check,
and failed the migration test's outdated table inventory. Adding
`audit_runs` to that exact inventory in `0cbd52f` made the focused
migration regression pass in 9.83 seconds. The other passing checks were
not repeated. This is combined evidence, not one all-green full run.

The Linux affected gate at `19df382` passed 31 checks in 33:12, including
the real SIGTERM cleanup regression and target compatibility tests.
Frontend checks/build, generated API drift, Compose validation, and
source secret scanning passed for the recorded dashboard gate.

The first Windows browser gate at `0cbd52f` passed 52 checks and failed
three. The focused retries below used `737220c`; its only code change
uses a disk file instead of a large buffer in the oversized-upload test.
The upload limit and assertions are unchanged.

- Oversized-upload and real document-permission journeys: 2 passed in
  2.4 minutes. The latter covers group grants, tenant isolation and
  revocation. The previous research-user login failure did not recur;
  its cause remains unconfirmed.
- Real Windows portal-to-worker audit: 1 passed in 9.3 minutes. It
  queued through the API, ran the real safe CLI, and downloaded the report.
- Frontend static checks: 50 files passed. LSP returned no diagnostics
  for the changed browser test. The browser run also built the frontend.
- The staged scan for `737220c` found no secrets.
- Real Linux portal-to-worker audit at the same clean revision: 1 passed
  in 4.3 minutes, including all 51 cases and report download equality.

## Windows artifact

The report records clean revision
`737220c51e5f6ffef1b8a90e86b48d806e5879d7`, safe profile, exit 0,
complete coverage, and 51 results without a runtime failure.

- Request: `5d5e52e2-4d8b-4b3e-9860-219cc84b623c`.
- Report: `6adf8ff4-5d6f-4bef-8c66-79956a3d1574`.
- SHA-256: `0086d3d175108905b040fd339036ef0370e4558e3d3a0b7c75cbfce58d7a316d`.

The browser asserted equality between the original artifact and download
bytes, and between the artifact, receipt and API checksums. The desktop
screenshot was inspected: finished/pass, 51 cases, complete coverage and
the JSON download control were visible.

Artifacts remain under
`data/audit-workspaces/dashboard-e2e-c5412886-4015-4602-af90-b47686e5b4c6/`.
Logs remain in `.superpowers/sdd/2026-10-04-audit-dashboard/`, including
`final-windows-backend.log`, `final-windows-migration-fix.log`,
`final-linux-affected.log`, `resume-windows-browser-two.log`, and
`resume-windows-real-audit.log`.

## Linux artifact

The Linux report also records clean revision `737220c`, safe profile,
exit 0, complete coverage and 51 results without a runtime failure.

- Request: `1de05c57-60e8-4b3e-b3a2-3d9689628997`.
- Report: `996b8a3b-439a-4315-bd6f-b09e97acee77`.
- SHA-256: `5dfbec3724a2575d8a4e329de039ff56a02ea68cbc2480fe5342d5bb56ea3332`.

The same browser assertions verified original/download byte equality and
artifact/receipt/API hash agreement. The screenshot was inspected and
showed the finished passing run with complete coverage. Copies remain in
`data/audit-reports/dashboard-linux-20261005/`; the copy's checksum matches
the receipt. The original synthetic workspace remains in the stopped runner
under `data/audit-workspaces/dashboard-e2e-73a927dd-b2aa-4b88-8f13-4f8063d2be39/`.
Log: `resume-linux-real-audit.log` in the dashboard ledger directory.

Only the three project-owned Linux verification containers were started
and stopped. They were not deleted. Ordinary local services and unrelated
processes were left alone.

## Limits

The first hosted run for PR #25 passed 209 unit checks and failed the
SIGTERM regression because ONNX printed a hardware-discovery warning
during dependency imports. Security jobs passed. A controlled native-warning
case reproduced the stderr failure on Linux: 1 failed, 1 passed.
The test now silences startup imports with the existing CLI context,
then restores streams before running the worker. Every shutdown assertion
is unchanged. Seven focused worker and stream-restoration checks passed
in 45.38 seconds; Ruff and mypy passed for the changed file. Production
code did not change. Hosted verification of this test correction is pending.
Logs: `pr-25-ci-failure.log`, `ci-native-import-red.log`, and
`ci-native-import-green.log` in the retained dashboard ledger directory.

The earlier inconclusive report and its owned fixtures are retained;
they were not replayed or purged. Memory exhaustion was observed during
the failed browser retry, but it does not establish every earlier failure's
cause. Windows had 6.73 GiB available RAM and 15.88 GiB commit headroom
before the passing retries.

These checks use synthetic documents and deterministic providers. They do
not establish real-model answer quality, production scale, company-upload
privacy, security certification, or crash-proof fencing after SIGKILL.
Injection trials, HTML exports, benchmarks, streaming and automatic purge
remain outside this slice. The branch is pushed in
[PR #25](https://github.com/HuzaifaAbdulRehman/ragelit/pull/25).
No merge or visibility change is recorded.
