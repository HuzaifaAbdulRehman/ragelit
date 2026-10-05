# HTML audit export verification

Recorded on 5 October 2026 from main merge 45df625. This record covers issue #21,
not the remaining MVP roadmap.

The protected `/api/v1/audits/{run_id}/report.html` endpoint renders saved JSON
only after the existing current-role, tenant, size, schema and checksum checks.
Original JSON downloads are unchanged. The portal offers Download HTML with the
same cancellation and denied-access handling.

The HTML contains no scripts, links or remote assets. Every variable is
HTML-escaped; the document and response carry a restrictive content policy.
Outcome and coverage remain those of the JSON report. Missing and unobserved
stages cannot turn an incomplete run into a pass.

Local evidence:

- `uv run --frozen pytest tests/unit/audits/test_html.py tests/unit/audits/test_reports.py tests/api/test_audits.py -q`:
  34 passed in 10.19 seconds after 12 expected RED failures.
- `uv run --frozen pytest tests/unit/audits/test_html.py tests/unit/audits/test_lab.py -q`:
  13 passed in 16.67 seconds. This also verifies the declared HTML MIME type and
  the web import boundary. Only the pure renderer joined the allowlist;
  privileged engine modules remain excluded.
- `npm run test -- tests/audit-html.spec.ts tests/audits.spec.ts --reporter=line`:
  11 passed in 1.3 minutes, including original JSON downloads and the Python
  renderer opened from a local file. Hostile values created no executable
  elements, set no window marker and made no HTTP requests. The screenshot
  was inspected.
- Ruff checks, mypy on the two changed production Python files, frontend checks
  on 51 files, generated contracts and TypeScript diagnostics passed.

Logs remain in the ignored `.superpowers/sdd/2026-10-04-audit-dashboard` folder.
This is focused local evidence; hosted CI results belong to the delivery PR.
No audit pack was repeated solely to add a report format. Injection tests,
benchmarks and clean-clone release verification remain separate work.

One independent branch review found no critical or important issues. Its minor
error-media-type finding was reproduced, corrected and covered by two passing
schema checks in 2.78 seconds. Generated contracts and frontend checks were
refreshed. Runtime and browser evidence remains applicable because only the
route documentation changed.
