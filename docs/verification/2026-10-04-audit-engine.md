# Audit engine verification

Recorded on 4 October 2026 in `feat/audit-engine`. This covers the
engine-first audit milestone, not the dashboard or the whole project.

## Source under test

Both final gates use base revision `793424694c029c706d4497a76421f30e1347c700`
with the same 15 staged implementation files overlaid. Reports correctly
record `git_dirty: true`. The implementation stayed frozen during these runs.
The staged binary-diff fingerprint is
`52adb1dda29d527eeff54c5ea1a8a0658cdffe65`, computed with
`git diff --cached --binary | git hash-object --stdin`.

## Final gates

- Linux: exit 0. Ruff formatting/lint and mypy passed 139 source files.
  `uv run --frozen pytest -v --tb=short --durations=10`, from `backend`,
  passed all 364 tests in 2938.39 seconds (48:58). The artifact validator
  also exited 0. This used Python 3.13.11, PostgreSQL 18, and Qdrant 1.15.4
  in a separate local Docker service namespace.
- Windows: exit 0. `powershell.exe -NoProfile -File scripts/verify.ps1`
  passed 182 unit checks in 56.92 seconds and 189 integration checks in
  5402.07 seconds (1:30:02), using PostgreSQL 16 and Qdrant 1.15.4.
  The report validator, Ruff/mypy checks, Compose configuration, generated
  API diff, frontend checks, and production build passed. All 41 browser
  journeys passed in 2.9 minutes. The Unix process-group cleanup test was
  skipped on Windows; this gate does not prove that Unix-specific check.
- Gitleaks found no leaks in the frozen 15-file staged implementation.
  Offline OSV 2.6.0 found no issues in the unchanged lockfiles, using the
  public advisory cache downloaded on 2 October. All 10 scanner behavior
  tests passed. This does not establish coverage of later advisories.

Logs remain in `.superpowers/sdd/2026-10-02-audit-engine/` as
`task-6-final-linux.log` and `task-6-final-windows.log`. The completed
Linux test containers are stopped, not deleted. Ordinary Windows test
services remain untouched.

## Reports and receipts

Each listed report contains 51 completed cases. Safe returns 0; the two
deliberately broken lab profiles return 1. The release validator checked
both platforms' exports and returned `audit_artifacts_valid` with exit 0.
That check ran without executing cases again.

Windows exports are under
`data/audit-reports/verification-f5f22526-5f11-4347-8f08-65de2c42e557/`:

- Safe: `ed4aeb33-068a-49d8-b009-d571ed3b2de7`;
  SHA-256 `156e3d427efead359c1cffd983b5fc3cf70c0832d6ae6d92e72b039ce84f9110`.
- Vulnerable: `85ee88f1-f767-4b40-94ee-101672a06656`;
  SHA-256 `057f44088537463962dbc8c691c75630880c361cee87d97b5a764d0e7bdf96e7`.
- Deny-all: `e78139b8-b0f9-4a3b-9d2f-2decaa956591`;
  SHA-256 `69d7bf9029e2ab9a42c5bbbae6c0b5956b34eb312a2d0ba41425b1a19dc9993b`.

Linux exports were copied to
`data/audit-reports/task6-final-linux-20261004/`:

- Safe: `b8aaf511-103d-48fe-81d3-e2edc039f8e2`;
  SHA-256 `eb199ca23788943cd9f597ffa5749c790460ceb4ca21956c4b90344835cbecfb`.
- Vulnerable: `4617687d-705d-4573-94a8-48c83b41706a`;
  SHA-256 `1068f728d2dd29c4bfacff2539f77cfa1ab1566584e31d75f7e906ea6c6dcb76`.
- Deny-all: `c75aa47b-c68b-49ca-a1fe-6582e45e1001`;
  SHA-256 `978397ae3568d734bd053b3e40fe2bb4105c3e20f311059cadab832a569e5024`.

Reports use template SHA-256
`4949502da16d689928823c2ecdc22647423f92fbf6a317e2856cefa6d51e0277`.
Only redacted report JSON and checksum receipts were exported; bindings and
source bodies were excluded. Checksums detect changes, not authenticity.

## Review fixes and boundaries

One fresh whole-branch review found two Important issues, both fixed with
failing regressions followed by passing focused checks. No Critical or
Minor findings were reported.

- Ambient `PGHOSTADDR` could redirect a validated loopback URL. The audit
  URL now pins the effective address, port, and bounded connection timeout
  through bootstrap, migrations, and both database engines. Both IPv4 and
  IPv6 regression cases failed before the fix; 20 workspace guards passed.
- An intentionally partial run left unused instances incomplete, blocking
  later full runs. Completed partial runs now mark only their unused
  instances skipped. Interrupted and historical instances remain protected.
  The 35 focused CLI/workspace tests passed, and Linux passed the real
  partial-then-full reuse test.

The final review happened before the final gate and commit, including
untracked implementation files. Passing unchanged suites are not rerun for
bookkeeping. Local logs and exports are retained so later sessions can
reuse the evidence.

CI now allows 90 minutes because the prior Linux suite alone took 38 minutes.
Its artifact gate requires complete safe/vulnerable/deny-all outcomes before
upload. At the original engine handoff, hosted CI had not run and the branch
had not been pushed or merged.

Retrieval observations cover fused results only. Synthetic fixtures do not
measure real-model quality, injection resistance, or production performance.
The audit dashboard and remote targets are outside this slice. Resistance
to a malicious local administrator racing filesystem/database changes is
not established. Bounded retained workspaces may need a fresh namespace or
separately approved recovery. No security certification is claimed.

## Follow-up after the requested push

The branch was pushed on 4 October. Hosted run
[37220374919](https://github.com/HuzaifaAbdulRehman/ragelit/actions/runs/37220374919)
passed its security job, including the current downloaded advisory databases.
Verification stopped at unit tests: 177 passed and five CLI tests failed.
ONNX wrote a native stderr warning that Python stream redirection did not catch.
Later integration, artifact-upload, and browser steps did not run.

The CLI quiet scope now redirects descriptors and flushes host C stdio before
redirection and before restoration. Two regression cases failed before the
descriptor fix; both failed again when extended to buffered native output.
After the flush fix, `uv run --frozen pytest tests/unit/audits/test_cli.py
tests/unit/audits/test_artifacts.py -q` passed 26 tests in 64.52 seconds on
Windows. Ruff formatting/lint and mypy passed for the two changed files.
The saved Windows release reports still validate with exit 0 without rerunning
cases. One focused review found the buffering gap; its amended review reports
no remaining findings. This does not establish cross-CRT or direct Win32 output
suppression. Hosted verification of the fix is pending; the old full gates are
evidence for the earlier implementation, not a full-suite claim for this change.

Issues 1 through 13 are closed against delivered main-branch evidence. M2/M3
tracking is backfilled in issues 15/16. M4 engine verification, the hosted CI
fix, dashboard, injection pack, and HTML export remain open in issues 17 to 21.
Isolation comparison, benchmarks, and the private v0.1 release remain open in
issues 22 to 24. The dashboard task plan awaits user review before implementation.
