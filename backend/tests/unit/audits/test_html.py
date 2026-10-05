import hashlib
from html.parser import HTMLParser

import pytest

from app.audits.contracts import Boundary, CanaryMatch
from app.audits.reports import build_report
from app.audits.scoring import score_case
from tests.unit.audits.support import FORBIDDEN, case, metadata, observation


class ParsedHtml(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.text: list[str] = []
        self.attributes: list[tuple[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        self.attributes.extend(attrs)

    def handle_data(self, data: str) -> None:
        self.text.append(data)


def test_html_download_declares_its_mime_type_in_the_api_contract() -> None:
    from fastapi import FastAPI

    from app.audit_jobs.api import router

    app = FastAPI()
    app.include_router(router)
    response = app.openapi()["paths"]["/audits/{run_id}/report.html"]["get"][
        "responses"
    ]["200"]
    assert response["content"]["text/html"]["schema"] == {"type": "string"}


def test_html_download_errors_remain_problem_json_in_the_api_contract() -> None:
    from fastapi import FastAPI

    from app.audit_jobs.api import router

    app = FastAPI()
    app.include_router(router)
    responses = app.openapi()["paths"]["/audits/{run_id}/report.html"]["get"][
        "responses"
    ]
    for status in (401, 403, 404, 409, 422, 503):
        assert responses[str(status)]["content"] == {
            "application/problem+json": {
                "schema": {"$ref": "#/components/schemas/ProblemDetail"}
            }
        }


@pytest.mark.parametrize("state", ["pass", "fail", "inconclusive"])
def test_html_preserves_outcome_and_stage_evidence(state: str) -> None:
    from app.audits.html import render_html

    observed = observation()
    if state == "fail":
        first = observed.boundaries[0].model_copy(
            update={
                "chunk_ids": (FORBIDDEN,),
                "canary_matches": (
                    CanaryMatch(canary_id="private-fixture", offsets=(4,)),
                ),
            }
        )
        observed = observed.model_copy(
            update={"boundaries": (first, *observed.boundaries[1:])}
        )
    report = build_report(
        (case(),),
        (score_case(case(), observed),),
        metadata(),
        runtime_failed=state == "inconclusive",
    )
    before = report.model_dump_json()
    content = render_html(report)
    parsed = ParsedHtml()
    parsed.feed(content)
    text = " ".join(parsed.text)
    assert f"Outcome: {state}" in text
    assert f"Exit code: { {'pass': 0, 'fail': 1, 'inconclusive': 2}[state] }" in text
    assert "ORG-001" in text
    assert "retrieval_raw" in text and "context" in text and "output_delivered" in text
    assert "observed" in text
    assert report.retrieval_notice in text and report.scope_notice in text
    assert (
        hashlib.sha256(before.encode()).hexdigest()
        == hashlib.sha256(report.model_dump_json().encode()).hexdigest()
    )
    if state == "fail":
        assert "First exposure: retrieval_raw" in text
        assert "private-fixture" in text and "4" in text
    assert "script" not in parsed.tags
    assert not any(
        name in {"src", "href", "srcdoc"} or name.startswith("on")
        for name, _ in parsed.attributes
    )


def test_html_escapes_hostile_values_without_network_or_executable_markup() -> None:
    from app.audits.html import render_html

    hostile = (
        "<script>window.auditExecuted=1</script>"
        '<img src="https://example.invalid/x" onerror="window.auditExecuted=2">'
    )
    report = build_report((case(),), (), metadata(), runtime_failed=True)
    report = report.model_copy(update={"required_case_ids": (hostile,)})
    parsed = ParsedHtml()
    parsed.feed(render_html(report))
    assert hostile in "".join(parsed.text)
    assert "script" not in parsed.tags and "img" not in parsed.tags
    assert not any(
        name in {"src", "href", "srcdoc"} or name.startswith("on")
        for name, _ in parsed.attributes
    )
    assert "Outcome: inconclusive" in " ".join(parsed.text)


def test_html_distinguishes_missing_unobserved_and_not_reached_evidence() -> None:
    from app.audits.html import render_html

    observed = observation()
    stages = tuple(
        stage.model_copy(update={"state": "unobserved"})
        if stage.boundary == Boundary.OUTPUT_CANDIDATE
        else stage.model_copy(update={"state": "not_reached"})
        if stage.boundary == Boundary.OUTPUT_DELIVERED
        else stage
        for stage in observed.boundaries
    )
    observed = observed.model_copy(update={"boundaries": stages})
    report = build_report((case(),), (score_case(case(), observed),), metadata())
    content = render_html(report)
    assert "unobserved" in content and "not_reached" in content
    assert "Outcome: inconclusive" in content
    empty = build_report((case(),), (), metadata())
    assert "No case results were recorded." in render_html(empty)
