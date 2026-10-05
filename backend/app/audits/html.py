from html import escape

from app.audits.reports import AuditReport

CONTENT_SECURITY_POLICY = (
    "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
)


def _text(value: object) -> str:
    return escape(str(value), quote=True)


def render_html(report: AuditReport) -> str:
    outcome = {0: "pass", 1: "fail", 2: "inconclusive"}[report.exit_code]
    sections: list[str] = []
    for result in report.results:
        rows = []
        for stage in result.observation.boundaries:
            matches = "; ".join(
                f"{match.canary_id}: count {match.count}, offsets "
                + ", ".join(str(offset) for offset in match.offsets)
                for match in stage.canary_matches
            )
            cells = (
                stage.sequence,
                stage.boundary.value,
                stage.state,
                stage.decision,
                ", ".join(str(chunk) for chunk in stage.chunk_ids) or "none",
                matches or "none",
                f"{stage.duration_ms:.3f}",
                str(stage.truncated).lower(),
            )
            rows.append(
                "<tr>" + "".join(f"<td>{_text(cell)}</td>" for cell in cells) + "</tr>"
            )
        first_exposure = (
            result.first_exposure.value if result.first_exposure else "none"
        )
        sections.append(
            f"<details><summary>{_text(result.case_id)}: "
            f"{_text(result.status)}</summary>"
            f"<p>Reason: {_text(result.reason.value)}</p>"
            f"<p>Coverage complete: {_text(str(result.coverage_complete).lower())}</p>"
            f"<p>First exposure: {_text(first_exposure)}</p>"
            f"<p>HTTP status: {_text(result.observation.http_status)}; "
            f"terminal: {_text(result.observation.terminal.value)}</p>"
            "<p>Scope hash: "
            f"{_text(result.observation.scope_hash or 'not observed')}</p>"
            '<div class="stages"><table><caption>Stage evidence</caption><thead><tr>'
            '<th scope="col">Sequence</th><th scope="col">Boundary</th>'
            '<th scope="col">State</th><th scope="col">Decision</th>'
            '<th scope="col">Chunk IDs</th>'
            '<th scope="col">Canary matches (redacted)</th>'
            '<th scope="col">Duration (ms)</th><th scope="col">Truncated</th>'
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div></details>"
        )
    required = "".join(
        f"<li>{_text(identifier)}</li>" for identifier in report.required_case_ids
    )
    metadata = "".join(
        f"<dt>{_text(key)}</dt><dd>{_text(value)}</dd>"
        for key, value in report.metadata.model_dump(mode="json").items()
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" '
        f'content="{_text(CONTENT_SECURITY_POLICY)}">'
        "<title>RAGelit synthetic audit report</title>"
        "<style>body{font:16px/1.5 system-ui,sans-serif;margin:2rem auto;"
        "padding:0 1rem;max-width:75rem;color:#17202a;background:#fff}"
        "h1,h2{line-height:1.2}summary{cursor:pointer;font-weight:600}"
        "details{border:1px solid #85929e;padding:1rem;margin:1rem 0}"
        "dt{font-weight:600}dd{margin:0 0 .7rem;overflow-wrap:anywhere}"
        "table{border-collapse:collapse;width:100%}th,td{border:1px solid #85929e;"
        "padding:.5rem;text-align:left;overflow-wrap:anywhere}"
        ".stages{overflow-x:auto}li{overflow-wrap:anywhere}"
        "summary:focus-visible{outline:3px solid #1f618d;outline-offset:3px}"
        "@media print{body{max-width:none;margin:0}details{break-inside:avoid}}"
        "</style></head><body><main><h1>RAGelit synthetic audit report</h1>"
        f"<p>Outcome: {_text(outcome)}</p><p>Exit code: {_text(report.exit_code)}</p>"
        f"<p>Run ID: {_text(report.run_id)}</p>"
        f"<p>Created: {_text(report.created_at.isoformat())}</p>"
        f"<p>Coverage complete: {_text(str(report.coverage_complete).lower())}</p>"
        f"<p>Runtime failed: {_text(str(report.runtime_failed).lower())}</p>"
        "<p>Inventory reason: "
        f"{_text(report.inventory_reason.value if report.inventory_reason else 'none')}"
        "</p>"
        f"<p>{_text(report.scope_notice)}</p><p>{_text(report.retrieval_notice)}</p>"
        "<p>Canary matches name opaque labels and offsets, never the matched values. "
        "Observed, not_reached and unobserved are distinct evidence states. "
        "This HTML is a view of the JSON result, not an independent audit.</p>"
        f"<h2>Required controls</h2><ul>{required}</ul><h2>Case results</h2>"
        + ("".join(sections) or "<p>No case results were recorded.</p>")
        + f"<h2>Run configuration</h2><dl>{metadata}</dl></main></body></html>"
    )
