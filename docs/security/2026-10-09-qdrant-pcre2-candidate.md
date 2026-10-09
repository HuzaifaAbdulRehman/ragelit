# Qdrant PCRE2 candidate, 9 October 2026

This local candidate changes only `libpcre2-8-0` from `10.46-1~deb13u2` to
`10.46-1~deb13u3`. [Debian](https://security-tracker.debian.org/tracker/CVE-2026-103111)
lists that revision as the fix for CVE-2026-103111. It is not deployed, selected
by Compose/CI or cleared for release.

The pinned Qdrant 1.19.2 base has digest
`sha256:b7b0444c4c351c970b98e90a6f89c2ee4287c65b44e52b4cb503fa5b2aa927ad`.
The resulting local linux/amd64 image has ID and repository digest
`sha256:288aa5558fa2746eac88e566bc87a6d6aa2c045f43763f8664e3acd6bdbc6996`,
tagged `ragelit-qdrant:pcre2-10.46-deb13u3`.

## What passed

Five focused tests passed in 20.53 seconds. The installed-package delta contains
only the two PCRE2 revisions. Startup configuration matches the base, and hashes
of the Qdrant binary, entrypoint and every static UI file are unchanged.
A disposable server inserted two synthetic vectors, retrieved the stored payload
and vector, found the expected nearest neighbor, then deleted both points.

The first candidate run passed three checks but stopped during readiness when
the server closed an early HTTP connection. A regression test reproduced that
exception before the harness learned to retry it. The 60-second readiness
deadline remains. The successful run still emitted the SDK's background
server-version warning; it was not suppressed. These checks establish basic
vector behavior, not the full application or model benchmark gate.

Ruff lint/format, strict mypy, Python compilation and LSP diagnostics passed.
Without the opt-in variable, one readiness test passed and four image checks
skipped; no Docker containers were started by that run.

The build and metadata containers used 128 MiB and half a CPU. Metadata probes
had no network and read-only filesystems. The server used 512 MiB and half a CPU,
a random loopback-only port and an owned anonymous storage volume. No existing
data was mounted. Cleanup verifies the ownership label before removing the
container and its anonymous volumes. A fresh label query found no containers.

## Reproduce locally

From the repository root, with Buildx resource-limit support:

```powershell
docker build --platform linux/amd64 --resource memory=128m --resource cpu-quota=50000 --tag ragelit-qdrant:pcre2-10.46-deb13u3 infra/qdrant-pcre2
$env:RAGELIT_QDRANT_CANDIDATE_IMAGE = "ragelit-qdrant:pcre2-10.46-deb13u3"
uv run --project backend --frozen pytest backend/tests/integration/test_qdrant_image_candidate.py -q
Remove-Item Env:RAGELIT_QDRANT_CANDIDATE_IMAGE
```

## Scoped scan

Docker Scout 1.24.0 scanned the exact local digest above on 9 October with
`--only-severity critical,high --locations`. It indexed 1,648 packages and reported
2 critical and 5 high findings in five packages. CVE-2026-103111 was absent.
The earlier base scan reported 2 critical and 6 high findings. Remaining findings
cover tinypool, brace-expansion, braces, source-map-js and Debian zlib.
Their [triage](2026-10-08-container-triage.md) remains open.

The scan exited 0 but warned that its temporary archive was locked during
cleanup. It did produce the complete finding list. No finding was suppressed.
Logs and JUnit evidence remain in ignored local storage under
`.superpowers/sdd/2026-10-09-postgres-zlib-candidate/`:

- `qdrant-pcre2-verified.xml`, SHA-256
  `78fde7c0896542bfc93ef7c30e70b5d68119b17623d2cd49e029bb703bac2ff6`.
- `qdrant-pcre2-scout.log`, SHA-256
  `7bf24be975b071b96eb6c9cf0d458c83f120c4ff84650a3168e910010853bd58`.

The failed inventory test against the unpatched base, failed startup attempt
and successful build log are retained alongside them. Running services,
existing data, Compose/CI and the application dependency lockfile are unchanged.
