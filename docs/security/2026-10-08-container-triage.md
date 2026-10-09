# Container image triage 8 October 2026

This is a partial reachability review, not a clean image scan or release sign-off. Docker Scout 1.24.0 inspected the existing linux/amd64 images. The initial scans did not suppress, upgrade or remove packages. The later repository-default update is recorded below.

| Image and scope | Observed findings |
| --- | --- |
| PostgreSQL 18-alpine, local ID 77f585114c32, critical/high | 2 critical, 23 high in 3 packages |
| Qdrant 1.15.4, local ID 6ac4807063bb, critical only | 12 critical in 6 packages |
| Qdrant 1.15.4, high Debian packages only | 36 high in 7 packages |
| Qdrant 1.19.1, registry digest prefix 0699e7733a6f, critical/high Debian packages only | 5 critical, 12 high in 6 packages |
| Qdrant 1.19.2, registry inventory prefix 0e8273b9130c, all critical/high packages | 2 critical, 6 high in 6 packages |
| Qdrant 1.19.2, pulled local digest b7b0444c4c35, all critical/high packages | 2 critical, 6 high in 6 packages |
| Qdrant 1.19.2, pulled local digest b7b0444c4c35, critical/high Debian packages only | 0 critical, 2 high in 2 packages |

Filtered counts are not whole-image totals. Registry inventory and pulled-image identifiers differ, so their counts are listed separately. The exact pulled 1.19.2 image has no critical Debian findings in this scan; high pcre2 and zlib findings remain. Its remaining two critical and four high findings come from the web UI's npm inventory. This is not a clean replacement image.

## What the package locations establish

- PostgreSQL's Go findings belong to /usr/local/bin/gosu 1.19, built with Go 1.24.6. The entrypoint calls gosu with the fixed postgres identity, and gosu replaces itself with the requested process. The two critical advisories require [TLS](https://pkg.go.dev/vuln/GO-2026-4337) or [HTTP/IDNA](https://pkg.go.dev/vuln/GO-2026-5026) behavior. No such path was identified in that startup use after reading the [gosu 1.19 entrypoint](https://github.com/tianon/gosu/blob/1.19/main.go) and [user-switch implementation](https://raw.githubusercontent.com/tianon/gosu/1.19/setup-user.go). This is a scoped inference, not a blanket exception for all 23 Go findings.
- PostgreSQL links libxml2 and zlib. DOCX extraction uses Python ElementTree, not PostgreSQL XML functions, but that does not prove every database-library path unreachable. Both packages remain open; the scanner reports no fixed Alpine libxml2 version for [CVE-2026-86140](https://ubuntu.com/security/CVE-2026-86140). Ubuntu fixes do not establish an Alpine fix.
- Qdrant's Vitest, tinypool and npm tar critical findings were identified through /qdrant/static/qdrant-web-ui.spdx.json. No node or npm executable was found on the container PATH. The entrypoint starts the Qdrant binary, not a Vitest server. The reviewed [Vitest server advisory](https://github.com/vitest-dev/vitest/security/advisories/GHSA-5xrq-8626-4rwp) has no identified runtime path here; the other packages still need separate review.
- Qdrant links glibc. [CVE-2026-5450](https://security-tracker.debian.org/tracker/CVE-2026-5450) involves a particular scanf format, not every libc call. Its application call path is unknown, and the 1.19.1 candidate remains below the Debian fixed version 2.41-12+deb13u4. The exact pulled 1.19.2 image has no glibc critical/high finding in the scoped scan.
- Scout marks OpenSSL CVE-2026-31789 critical, but the [upstream advisory](https://openssl-library.org/news/secadv/20260407.txt) rates it low and limits it to 32-bit certificate-printing paths. This inspected image is amd64. The other OpenSSL findings are not dismissed by that architecture check.

## Advisory-level follow-up

Read-only inspections at source `a3b6b53` used the exact Qdrant image above and PostgreSQL image ID `sha256:77f585114c32fbca283dc835b0596f4e52b51b4c6662d7810b2f4084f60a1873`. Each disposable container had no network, no data mounts, a read-only filesystem, a 128 MiB memory cap and half a CPU. They ran metadata commands, not either database server, and were removed afterward. No new vulnerability scan was run.

Qdrant's startup script launches its Rust binary. Its dynamic library listing includes libc, libunwind, libgcc, libm and liblzma, but not PCRE2 or zlib. No node/npm executable was found on PATH, and no `node_modules` directory was found within three levels of `/qdrant`. Compiled browser JavaScript is present under `/qdrant/static/assets`; the absence of Node does not clear those assets.

| Component and advisory | Required behavior | Evidence and remaining action |
| --- | --- | --- |
| tinypool 1.1.1, CVE-2026-104848 and CVE-2026-104849 | Prototype pollution reaching Node worker options or a supplied `run()` options object. See the upstream [worker-options](https://github.com/tinylibs/tinypool/security/advisories/GHSA-5gmw-xhrv-c9v3) and [run-options](https://github.com/tinylibs/tinypool/security/advisories/GHSA-85c8-ppgw-ccpr) advisories. | Present in the UI inventory. No Node service execution path was identified in this image. This assessment excludes rebuilding the UI and does not waive other npm findings. Scout classified these critical; upstream labels both high. Neither score was silently substituted. |
| brace-expansion 1.1.18, CVE-2026-102276 and CVE-2026-102278 | Untrusted brace patterns reaching the recursive [comma parser](https://github.com/juliangruber/brace-expansion/security/advisories/GHSA-6j4f-fj2g-mc7p) or [nested expansion](https://github.com/juliangruber/brace-expansion/security/advisories/GHSA-qhr7-859c-m2p7). | Present in the UI inventory. Browser/build call paths were not established; keep open. Version 1.1.20 addresses both advisories on this release line. Changing RAGelit's lockfile would not replace Qdrant's bundled assets. |
| braces 3.0.3, CVE-2026-93687 | Deeply nested patterns reaching recursive AST walkers, described in the [upstream report](https://github.com/micromatch/braces/issues/70). A [follow-up issue](https://github.com/micromatch/braces/issues/73) records the missing fixed release. | Present in the UI inventory. Browser/build call paths remain unverified; keep open. Do not invent a fixed version or treat the missing Node executable as a browser safety test. |
| source-map-js 1.2.1, CVE-2026-93749 | Attacker-controlled indexed maps with extreme section offsets reaching `SourceNode`. See the [upstream report](https://github.com/7rulnik/source-map-js/issues/76). | Present in the UI inventory. Its browser/build call path remains unverified; keep open. [Version 1.2.2](https://github.com/7rulnik/source-map-js/releases/tag/v1.2.2) is the fix. RAGelit's own frontend already locks 1.2.2; that does not patch Qdrant's UI. |
| PCRE2 10.46-1~deb13u2, CVE-2026-103111 | Attacker-controlled patterns compiled with JIT and matched using a growable JIT stack, per the [maintainer advisory](https://github.com/PCRE2Project/pcre2/security/advisories/GHSA-r9hj-j2rw-4q3m). | Installed in Qdrant's image but absent from the binary's dynamic dependencies. Static code and other commands were not ruled out; keep open. [Debian](https://security-tracker.debian.org/tracker/CVE-2026-103111) lists 10.46-1~deb13u3 as the trixie fix. |
| Qdrant zlib 1:1.3.dfsg+really1.3.1-1+b1, CVE-2026-85091 | Non-blocking gzip writes followed by `gzprintf()` or `gzvprintf()` after a stalled write. | Installed but absent from the binary's dynamic dependencies. [Debian](https://security-tracker.debian.org/tracker/CVE-2026-85091) marks this release vulnerable and unfixed, while the upstream description starts at 1.3.1.2. Keep the distribution/version-range discrepancy and unverified call paths open. |
| PostgreSQL zlib 1.3.2-r0, CVE-2026-85091 | The same stalled non-blocking gzip-write path. | Dynamically linked. [Alpine's v3.24 security feed](https://secdb.alpinelinux.org/v3.24/main.json) lists 1.3.2-r1 as the fix. This is a remediation target, not an installed patch or a demonstrated application exploit. |
| PostgreSQL libxml2 2.13.9-r2, CVE-2026-86140 | The vulnerable `xmlSnprintfElements()` path described by the [package advisory](https://ubuntu.com/security/CVE-2026-86140). | Dynamically linked. DOCX parsing uses Python ElementTree; no PostgreSQL XML call was identified in application code. Other database/library paths remain unverified. The inspected Alpine feed had no fix entry for this CVE; an Ubuntu fix is not an Alpine fix. Keep open. |

The UI inventory establishes package versions, not executable reachability. These assessments are not approved risk acceptance, scanner suppressions or a completed container-security gate. The other PostgreSQL Go findings still require their own advisory-level review.

On 9 October, the [gosu release page](https://github.com/tianon/gosu/releases/tag/1.19)
still identified 1.19 as the latest release, built with Go 1.24.6. Its
[security policy](https://github.com/tianon/gosu/blob/1.19/SECURITY.md) calls for
`govulncheck` reachability analysis rather than rebuilding for unused Go APIs.
A [binary-symbol scan](2026-10-09-gosu-symbol-review.md) now covers gosu in the
exact PostgreSQL candidate. It detected no vulnerable symbols for the 23 Scout
Go CVEs, but retained three package-level warnings across its full report.
Neither this evidence nor the maintainer policy clears the image or approves
a release exception.

## Disposable 1.19.2 compatibility check

[Qdrant 1.19.2](https://github.com/qdrant/qdrant/releases/tag/v1.19.2), published on 5 October, was tested at digest `sha256:b7b0444c4c351c970b98e90a6f89c2ee4287c65b44e52b4cb503fa5b2aa927ad`. The native safe audit ran against separate loopback-only Qdrant and PostgreSQL containers with ephemeral storage. It used clean source `a4d6f9112eba16979e8faff62b8aeea6e328d430`, deterministic embeddings and fixture generation, not a language model.

Run `38ccebcf-61d4-40a8-b363-91f6ef7baaa0` completed all 51 cases with complete coverage and exit 0. The original report reader validated its JSON and SHA-256 receipt. This tests the safe access-control path, not every application endpoint, the vulnerable/deny-all calibration profiles or real-model behavior. The two owned test containers were removed; existing service volumes and compose pins were untouched. Saved evidence remains in ignored local storage.

## Next action

The [9 October PostgreSQL zlib candidate](2026-10-09-postgres-zlib-candidate.md)
now passes focused package, startup-configuration and compressed-backup checks.
Only zlib changed to Alpine's fixed revision. It has not been deployed or selected
by Compose/CI; other findings and the container-security gate remain open.

After this initial triage, the user approved updating fresh-data defaults to the exact 1.19.2 image above and matching Python client 1.19.1. Compose and CI now select that pair. The [full compatibility gate](https://github.com/HuzaifaAbdulRehman/ragelit/actions/runs/37809980310) passed at `44a70aa31598c2d69094f31fae9d8fa3b225db76`: 786 unit tests, 251 integration tests and 57 browser journeys, plus static, build, secret and locked-dependency checks. All three validated audit export sets were uploaded.

Running services and existing volumes were not changed. The [operator warning](../../OPERATIONS.md#existing-qdrant-data) explains why an older data volume must not be upgraded directly or downgraded afterward. This fresh-data pass does not test storage migration or clear the image findings.

Prioritize the linked runtime libraries, then review build-inventory findings against each advisory's prerequisites. Do not replace missing reachability evidence with a blanket scanner exclusion or call these images production-cleared.

## Reproduce the scoped scan

```powershell
docker scout cves local://postgres:18-alpine --only-severity critical,high --locations
docker scout cves local://qdrant/qdrant:v1.15.4 --only-severity critical --locations
docker scout cves local://qdrant/qdrant:v1.15.4 --only-severity high --only-package-type deb --locations
docker scout cves registry://qdrant/qdrant:v1.19.1 --only-severity critical,high --only-package-type deb --locations
docker scout cves registry://qdrant/qdrant:v1.19.2 --only-severity critical,high --locations
docker scout cves local://qdrant/qdrant@sha256:b7b0444c4c351c970b98e90a6f89c2ee4287c65b44e52b4cb503fa5b2aa927ad --only-severity critical,high --only-package-type deb --locations
docker scout cves local://qdrant/qdrant@sha256:b7b0444c4c351c970b98e90a6f89c2ee4287c65b44e52b4cb503fa5b2aa927ad --only-severity critical,high --locations
```

Counts apply to the inspected image identifiers and advisory data on this date. Registry tags and advisory databases can change. The candidate ran only in disposable test services; no existing service image or volume was changed.
