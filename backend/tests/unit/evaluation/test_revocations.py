from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.audits.contracts import Boundary, Terminal
from app.audits.seeding import FixtureCitingProvider
from app.evaluation.reports import UtilityQueryRecord
from tests.unit.evaluation.report_support import bindings, record
from tests.unit.evaluation.test_runner import capture_workspace as capture_workspace


def denied() -> UtilityQueryRecord:
    before = record()
    stages = tuple(
        stage.model_copy(
            update={
                "chunk_ids": (),
                "state": "observed"
                if stage.boundary
                in {
                    Boundary.RETRIEVAL_RAW,
                    Boundary.RETRIEVAL_ACCEPTED,
                    Boundary.CONTEXT,
                }
                else "not_reached",
            }
        )
        for stage in before.observation.boundaries
    )
    return before.model_copy(
        update={
            "observation": before.observation.model_copy(
                update={"terminal": Terminal.ABSTAINED, "boundaries": stages}
            ),
            "citations": (),
            "answer_label_match": False,
        }
    )


def test_revocation_requires_same_actor_positive_before_and_observed_denial() -> None:
    from app.evaluation.revocations import revocation_measurement

    measured = revocation_measurement(
        bindings()[0],
        record(),
        denied(),
        update_committed=True,
        elapsed_ms=10.0,
        grant_restore="restored",
    )
    assert measured.positive_before
    assert measured.denial_observed
    assert measured.coverage_complete
    assert measured.revocation_to_confirmation_ms == 10.0
    assert measured.before.citations[0].version_id == bindings()[0].version_id
    assert measured.after is not None
    assert measured.after.citations == ()


@pytest.mark.parametrize("mode", ["still_visible", "unobserved", "bad_restore"])
def test_unproven_enforcement_or_restore_never_becomes_zero_delay(mode: str) -> None:
    from app.evaluation.revocations import revocation_measurement

    after = record() if mode == "still_visible" else denied()
    if mode == "unobserved":
        after = after.model_copy(
            update={
                "observation": after.observation.model_copy(
                    update={
                        "boundaries": tuple(
                            stage.model_copy(update={"state": "unobserved"})
                            if stage.boundary == Boundary.CONTEXT
                            else stage
                            for stage in after.observation.boundaries
                        )
                    }
                )
            }
        )
    measured = revocation_measurement(
        bindings()[0],
        record(),
        after,
        update_committed=True,
        elapsed_ms=10.0,
        grant_restore="failed" if mode == "bad_restore" else "restored",
    )
    assert measured.elapsed_ms == 10.0
    assert not measured.coverage_complete
    assert measured.revocation_to_confirmation_ms is None


def test_missing_positive_control_cannot_claim_fast_revocation() -> None:
    from app.evaluation.revocations import revocation_measurement

    measured = revocation_measurement(bindings()[0], denied())
    assert not measured.positive_before
    assert measured.revocation_to_confirmation_ms is None
    assert not measured.update_committed
    assert measured.grant_restore == "not_needed"


@pytest.mark.parametrize("duration", [-1.0, float("nan"), float("inf")])
def test_invalid_revocation_clock_is_rejected(duration: float) -> None:
    from app.evaluation.revocations import revocation_measurement

    with pytest.raises(ValueError):
        revocation_measurement(
            bindings()[0],
            record(),
            denied(),
            update_committed=True,
            elapsed_ms=duration,
            grant_restore="restored",
        )


def test_different_actor_or_query_cannot_enter_a_paired_revocation_record() -> None:
    from app.evaluation.revocations import revocation_measurement

    with pytest.raises(ValueError):
        revocation_measurement(
            bindings()[0],
            record(),
            record(1),
            update_committed=True,
            elapsed_ms=10.0,
            grant_restore="restored",
        )
    with pytest.raises(ValueError):
        revocation_measurement(
            bindings()[0],
            record(),
            denied().model_copy(update={"actor_id": "org-2-owner"}),
            update_committed=True,
            elapsed_ms=10.0,
            grant_restore="restored",
        )


def test_after_observation_without_committed_update_is_rejected() -> None:
    from app.evaluation.revocations import revocation_measurement

    with pytest.raises(ValueError):
        revocation_measurement(bindings()[0], record(), denied(), elapsed_ms=10.0)


@pytest.mark.parametrize("after_fails", [False, True])
def test_driver_restores_actual_original_grants_after_the_measurement(
    capture_workspace: SimpleNamespace,
    after_fails: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.evaluation import revocations
    from app.evaluation.runner import QueryCapture

    fixture = capture_workspace
    original = {"visibility": "organization", "user_ids": [], "group_ids": []}
    monkeypatch.setattr(revocations, "_permissions", lambda *args: original)
    clock = iter((10.0, 10.01))
    monkeypatch.setattr(revocations, "perf_counter", lambda: next(clock))
    changes: list[dict[str, Any]] = []
    questions: list[str] = []

    app = cast(FastAPI, fixture.app)

    @app.patch("/api/v1/documents/{document_id}")
    async def grant(document_id: str, request: Request) -> JSONResponse:
        assert document_id == str(bindings()[0].document_id)
        assert request.headers["authorization"].startswith("Bearer synthetic-")
        changes.append(await request.json())
        return JSONResponse({"id": document_id})

    def capture(workspace: Any, corpus: Any, query: Any, **kwargs: Any) -> QueryCapture:
        assert query.actor_id == fixture.corpus.queries[0].actor_id
        assert query.id == "org-1:change-notice"
        questions.append(query.id)
        if len(questions) == 1:
            return QueryCapture(record(), False)
        assert len(changes) == 1
        if after_fails:
            raise RuntimeError("ExceptionSecretMarker")
        return QueryCapture(denied(), False)

    monkeypatch.setattr(revocations, "capture_utility_query", capture)
    measured = revocations.capture_revocation(
        fixture.workspace,
        fixture.corpus,
        fixture.corpus.queries[0],
        provider=FixtureCitingProvider(),
    )
    assert changes == [
        {"visibility": "restricted", "user_ids": [], "group_ids": []},
        original,
    ]
    assert len(questions) == 2
    assert measured.update_committed
    assert measured.grant_restore == "restored"
    assert measured.coverage_complete is not after_fails
    assert measured.runtime_failed is after_fails
    assert "ExceptionSecretMarker" not in measured.model_dump_json()
    if not after_fails:
        assert measured.revocation_to_confirmation_ms == pytest.approx(10.0)


def test_driver_does_not_mutate_grants_when_before_control_is_missing(
    capture_workspace: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.evaluation import revocations
    from app.evaluation.runner import QueryCapture

    monkeypatch.setattr(
        revocations,
        "capture_utility_query",
        lambda *args, **kwargs: QueryCapture(denied(), False),
    )

    def unreachable(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("missing baseline reached a permission mutation")

    monkeypatch.setattr(revocations, "_permissions", unreachable)
    result = revocations.capture_revocation(
        capture_workspace.workspace,
        capture_workspace.corpus,
        capture_workspace.corpus.queries[0],
        provider=FixtureCitingProvider(),
    )
    assert not result.update_committed
    assert result.grant_restore == "not_needed"
    assert result.revocation_to_confirmation_ms is None
    assert capture_workspace.events == []
