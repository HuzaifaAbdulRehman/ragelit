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
Only zlib changed to Alpine's fixed revision. The candidate checks did not change
Compose/CI or deploy it. The later promotion below selects it for fresh installs;
other findings and the container-security gate remain open.

After this initial triage, the user approved updating fresh-data defaults to the exact 1.19.2 image above and matching Python client 1.19.1. Compose and CI selected that pair at that stage. The [full compatibility gate](https://github.com/HuzaifaAbdulRehman/ragelit/actions/runs/37809980310) passed at `44a70aa31598c2d69094f31fae9d8fa3b225db76`: 786 unit tests, 251 integration tests and 57 browser journeys, plus static, build, secret and locked-dependency checks. All three validated audit export sets were uploaded.

Running services and existing volumes were not changed. The [operator warning](../../OPERATIONS.md#existing-qdrant-data) explains why an older data volume must not be upgraded directly or downgraded afterward. This fresh-data pass does not test storage migration or clear the image findings.

Prioritize the linked runtime libraries, then review build-inventory findings against each advisory's prerequisites. Do not replace missing reachability evidence with a blanket scanner exclusion or call these images production-cleared.

## Fix availability recheck (9 October)

At clean source `415f1b7`, a disposable container checked signed Debian indexes
for the exact Qdrant 1.19.2 image above. The probe, including cleanup,
exited 0 in 24.63 seconds.
It would upgrade only `libpcre2-8-0` from `10.46-1~deb13u2` to
`10.46-1~deb13u3`, with no additions or removals. This matches the
[Debian fix](https://security-tracker.debian.org/tracker/CVE-2026-103111).
No package was installed. The container had a read-only root filesystem,
128 MiB RAM and half a CPU, temporary package-index storage, no published ports
and no existing data mounts. It was removed, and a fresh label query found none.

The log is retained under
`.superpowers/sdd/2026-10-09-postgres-zlib-candidate/` as
`pcre2-availability-2949ca308a4545c5a5d4ddfdd9403246.log`, SHA-256
`9db19ad9ecb04ed7c76eb72cc5a4083f268fc66298ce1257ac750c72a999e4b5`.
The user then approved a local, pinned single-package candidate. The
[PCRE2 candidate](2026-10-09-qdrant-pcre2-candidate.md) now passes inventory,
startup, binary/UI and basic vector read/write checks. Its scan no longer
reports the PCRE2 finding. Those candidate checks did not select it in Compose/CI
or deploy it. Remaining findings and release gates are still open.

The refreshed [Alpine v3.24 feed](https://secdb.alpinelinux.org/v3.24/main.json)
still lists zlib `1.3.2-r1` for CVE-2026-85091, but has no libxml2 fix entry for
CVE-2026-86140. Its saved snapshot `alpine-v3.24-security-refresh.json` has
SHA-256 `715e2255b2022ce2ed976ede172cf7eb519ba5ce6dcd2dbddaf3049b2473431d`.
An absent feed entry does not establish that every available package is unfixed.

Qdrant's configured repositories offer no newer zlib candidate. The
[Debian tracker](https://security-tracker.debian.org/tracker/CVE-2026-85091)
still marks its release vulnerable. The
[upstream discussion](https://github.com/madler/zlib/issues/1310)
disputes the affected range; the issue author also reports that the supplied
PoC can trigger behavior before the named range. Neither observation settles
the installed package's classification. Keep it open, without suppression.
No exploit PoC or new whole-image scan was run.

## Remaining-path review (9 October)

The exact PCRE2 candidate contains the Qdrant UI 0.2.19 inventory. Its SPDX
relationships place tinypool 1.1.1 under Vitest 3.2.7, and Vitest is a development
dependency of the UI. The matching upstream
[package file](https://raw.githubusercontent.com/qdrant/qdrant-web-ui/v0.2.19/package.json)
lists Vitest under `devDependencies` and uses Vite to build the static dashboard.
Together with the previous no-Node startup inspection, this supports a scoped
no-identified-runtime-path assessment for the two tinypool findings. It does
not prove every bundled JavaScript dependency absent or approve suppression.

The [Alpine 3.24 recipe](https://raw.githubusercontent.com/alpinelinux/aports/3.24-stable/main/libxml2/APKBUILD)
still names libxml2 2.13.9-r2 and lists no fix for CVE-2026-86140. In
[libxml2 2.13.9](https://raw.githubusercontent.com/GNOME/libxml2/v2.13.9/valid.c),
`xmlSnprintfElements` is called while formatting a failed element-content
validation diagnostic. [PostgreSQL 18.6's reviewed xml.c](https://raw.githubusercontent.com/postgres/postgres/REL_18_6/src/backend/utils/adt/xml.c)
sets `NOENT` and `DTDATTR`, not `DTDVALID`; no direct call to the reviewed
DTD-validation APIs was found there. RAGelit extracts DOCX with ElementTree,
not SQL XML functions. This narrows the plausible application path, but does not
prove transitive unreachability or clear arbitrary SQL, extensions or other
libxml2 consumers. Keep the installed-library finding open.

Brace-expansion, braces and source-map-js remain inventory findings with
unverified browser/build paths. [Debian](https://security-tracker.debian.org/tracker/CVE-2026-85091)
still marks Qdrant's zlib release vulnerable and unfixed. No supported
same-release remediation was established in this review. Do not substitute an
unrelated distribution package, delete only the SPDX file or silently accept risk.

The extracted inventory is retained as `qdrant-ui-inventory.spdx.json` under
`.superpowers/sdd/2026-10-09-postgres-zlib-candidate/`, SHA-256
`c268249fa5e5a789ccc70ee91e14d8a39b38b7213b2d031dcffaf4efff34ba3b`.
That directory also holds the four official source snapshots. The stopped
extraction container was ownership-checked and removed with its anonymous
volumes; independent queries confirmed cleanup. It never ran a server and used
no existing data. No new scan or full suite ran, and no release gate was closed.

The user approved [promoting both tested library patches](../verification/2026-10-07-local-release-checkpoint.md#fresh-install-patch-promotion-9-october)
into fresh-install Compose and CI defaults. Existing services and data remain
untouched. Removing or rebuilding other components would be separate work;
remaining findings and the real-model benchmark are still open.

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
