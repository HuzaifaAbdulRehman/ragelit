# Container image triage 8 October 2026

This is a partial reachability review, not a clean image scan or release sign-off. Docker Scout 1.24.0 inspected the existing linux/amd64 images. No package was suppressed, upgraded or removed.

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

## Disposable 1.19.2 compatibility check

[Qdrant 1.19.2](https://github.com/qdrant/qdrant/releases/tag/v1.19.2), published on 5 October, was tested at digest `sha256:b7b0444c4c351c970b98e90a6f89c2ee4287c65b44e52b4cb503fa5b2aa927ad`. The native safe audit ran against separate loopback-only Qdrant and PostgreSQL containers with ephemeral storage. It used clean source `a4d6f9112eba16979e8faff62b8aeea6e328d430`, deterministic embeddings and fixture generation, not a language model.

Run `38ccebcf-61d4-40a8-b363-91f6ef7baaa0` completed all 51 cases with complete coverage and exit 0. The original report reader validated its JSON and SHA-256 receipt. This tests the safe access-control path, not every application endpoint, the vulnerable/deny-all calibration profiles or real-model behavior. The two owned test containers were removed; existing service volumes and compose pins were untouched. Saved evidence remains in ignored local storage.

## Next action

Keep the current compose pins until the candidate completes the remaining compatibility checks and its findings are reviewed. Prioritize the linked runtime libraries, then review build-inventory findings against each advisory's prerequisites. Do not replace missing reachability evidence with a blanket scanner exclusion or call these images production-cleared.

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
