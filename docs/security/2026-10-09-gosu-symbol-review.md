# Gosu binary-symbol review, 9 October 2026

The PostgreSQL candidate's 23 Go findings now have binary-symbol evidence.
This is not a container-wide security clearance or an approved scanner exclusion.

The inspected image was
`sha256:06f26ffef1b4274d2c0378725007d995a317f58635c24bf7b790436700cf7ffa`.
Its `/usr/local/bin/gosu` identified itself as gosu v1.19.0, built with
Go 1.24.6 for linux/amd64, with CGO disabled. The extracted binary SHA-256 was
`52c8749d0142edd234e9d6bd5237dff2d81e71f43537e2f4f66f75dd4b243dd0`.

The official govulncheck v1.8.0 was built locally with Go 1.26.5, one build worker
and a 256 MiB Go memory target. Its version command reported the online Go
database updated on 7 October at 14:10:51 UTC. That database was not frozen
as an offline snapshot.

```console
govulncheck -mode binary -scan symbol -format sarif gosu
```

The parsed SARIF contains 45 advisory results: 42 module-level notes, three
package-level warnings and no symbol-level errors. All 23 Go CVEs in the saved
critical/high Scout report matched aliases in this report. Of those, 22 were
module-level notes and CVE-2026-39822 was a package-level warning. None had a
detected vulnerable symbol.

The three package warnings remain visible:

| Go advisory | CVE | Package | Required behavior |
| --- | --- | --- | --- |
| [GO-2026-4602](https://pkg.go.dev/vuln/GO-2026-4602) | CVE-2026-27139 | os | Directory enumeration on a file opened within an os.Root can expose metadata outside that root. |
| [GO-2026-4864](https://pkg.go.dev/vuln/GO-2026-4864) | CVE-2026-32282 | internal/syscall/unix | A symlink replacement race during Root.Chmod can change permissions outside the root. |
| [GO-2026-4970](https://pkg.go.dev/vuln/GO-2026-4970) | CVE-2026-39822 | os | Root-based file opening follows a final symlink when the path ends in a slash. |

These prerequisites come from the official Go advisories, rechecked on
9 October. Gosu 1.19's reviewed
[main](https://raw.githubusercontent.com/tianon/gosu/1.19/main.go) and
[user-switch implementation](https://raw.githubusercontent.com/tianon/gosu/1.19/setup-user.go)
contain no Root or directory-enumeration calls. That supports the scoped
no-identified-path assessment; it is not a transitive source call-graph proof.
The package warnings remain recorded. No rebuild, rescan or suppression was
used to change their status.

The full report is retained in ignored local storage under
`.superpowers/sdd/2026-10-09-postgres-zlib-candidate/govulncheck/`,
as `gosu-symbols.sarif.json`. Its SHA-256 is
`cd79285b05c2a338106284a25e260e8ca58b128df963d4b4d7298ed5ca554069`.
The checker binary SHA-256 is
`e0a2bdb28ffe5d7673a3ba68a9bbf1740c131922814d501916209d1da60fc4fd`.

A structured-report exit of 0 does not mean no findings. The
[checker documentation](https://pkg.go.dev/golang.org/x/vuln/cmd/govulncheck)
explains that binary scans inspect symbols, omit source call graphs, and may
miss behavior outside their analysis. The result supports a scoped
no-vulnerable-symbol-detected assessment for this exact binary and advisory
data. No maintainer exclusion wrapper or blanket suppression was used.

The libxml2 finding and Qdrant findings remain open. Compose, CI, existing
services and volumes were unchanged. The extracted binary was inspected,
not executed on Windows or installed into a service.
