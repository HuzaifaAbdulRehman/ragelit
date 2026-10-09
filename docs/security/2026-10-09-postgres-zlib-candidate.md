# PostgreSQL zlib candidate, 9 October 2026

This is a tested local candidate, not a deployed image or release sign-off.
It changes only zlib from 1.3.2-r0 to 1.3.2-r1 on the pinned official PostgreSQL
18.6 Alpine 3.24 base. [Alpine's security feed](https://secdb.alpinelinux.org/v3.24/main.json)
lists that revision as the fix for CVE-2026-85091.

The base registry digest was rechecked before building:
`sha256:77f585114c32fbca283dc835b0596f4e52b51b4c6662d7810b2f4084f60a1873`.
The resulting local linux/amd64 image has ID
`sha256:06f26ffef1b4274d2c0378725007d995a317f58635c24bf7b790436700cf7ffa`.

## What passed

The inventory test failed against the original base, then all three checks passed
against the candidate in 46.12 seconds. The installed-package delta contains only
the two zlib revisions. Entrypoint, command, environment, user, volume declarations
and exposed-port declarations match the base.

A disposable database restored a gzip-compressed custom-format dump containing
100 synthetic document rows and 950,000 body characters. The readiness check waits
for the final PostgreSQL process, not its temporary initialization server.
Ruff lint/format, strict mypy and Python compilation passed for the new test file.

The build used 128 MiB and half a CPU. Inventory containers had the same limits,
no network and read-only filesystems. The database had 256 MiB and half a CPU,
128 MiB tmpfs data storage, no network, no published ports and no host mounts.
Cleanup verified its ownership label before removing it and any owned anonymous
volumes. No candidate container remains.

## Run the check

From the repository root, using a Buildx version that supports resource limits:

```powershell
docker build --platform linux/amd64 --resource memory=128m --resource cpu-quota=50000 --tag ragelit-postgres:zlib-1.3.2-r1 infra/postgres-zlib
cd backend
$env:RAGELIT_POSTGRES_CANDIDATE_IMAGE = "ragelit-postgres:zlib-1.3.2-r1"
uv run --frozen pytest tests/integration/test_postgres_image_candidate.py -q
Remove-Item Env:RAGELIT_POSTGRES_CANDIDATE_IMAGE
```

Without the opt-in variable, these tests skip and start no containers. The build
context contains only the candidate Dockerfile and its ignore file. This run used
Docker Engine 29.7.2 and Buildx 0.36.1.

## Scoped scan result

Docker Scout 1.24.0 scanned the exact local image ID above on 9 October with
`--only-severity critical,high`. It indexed 77 packages and reported 2 critical
and 22 high findings across two package inventories: Go stdlib 1.24.6 in gosu
(23 findings), and libxml2 2.13.9-r2 (one high finding). CVE-2026-85091 was absent.
The earlier base scan reported 2 critical and 23 high; this result is consistent
with removing the zlib finding, not clearing the remaining packages.

```powershell
docker scout cves local://ragelit-postgres:zlib-1.3.2-r1 --only-severity critical,high --format sarif --output .superpowers/sdd/2026-10-09-postgres-zlib-candidate/candidate-cves.sarif.json
```

The command exited 0 and its SARIF report parsed successfully. Scout warned that
its temporary image archive was still locked during cleanup; it did write the
report. The report and scan log remain in ignored local storage under
`.superpowers/sdd/2026-10-09-postgres-zlib-candidate/`. The report SHA-256 is
`503b6b3975b4b5a0dfa6da01dc235086f6b7f4aac385843ce7c89af8257d9358`.

## Application audit checks

At clean source `cfb8ec7`, 26 DB-backed tests passed before an isolation setup
failure. A tiny probe reproduced exhaustion of Qdrant's 256 MiB temporary
storage at the fifth collection. Switching the owned Qdrant service to
disposable disk-backed storage required no application code change.

The focused three-strategy isolation test then passed in 1241.46 seconds.
Each strategy completed 51 cases, with expected exits 0, 0 and 1. Native readers
validated its reports and the original access/injection exports. All owned
test services and the anonymous disk volume were removed.

The [local checkpoint](../verification/2026-10-07-local-release-checkpoint.md#candidate-application-audit-checks-9-october)
records the command, source, storage limits and retained artifacts.
The full 259-case candidate suite is not complete: 232 cases remain unexecuted.

## What this does not establish

Compose and CI still select the existing PostgreSQL image. No existing service,
database or volume was started, restarted or migrated by these checks.
The unchanged application baseline passes
[main CI at e646524](https://github.com/HuzaifaAbdulRehman/ragelit/actions/runs/37821775490);
that is not full application compatibility evidence for this candidate.

No exploit reproduction was run. The remaining
[container findings](2026-10-08-container-triage.md), real-model generation/security
and revocation measurements, and release sign-off remain open. Do not use this
patch to describe either container image as security-cleared.
