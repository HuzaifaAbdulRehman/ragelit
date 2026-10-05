import pytest

from app.audits.observer import AuditObserver


@pytest.mark.parametrize(
    "code,expected",
    [
        ("membership_inactive", "membership_inactive"),
        ("authentication_failed", "authentication_failed"),
        (None, "authentication_failed"),
        ("SyntheticSecretMustNotBeExported", "authentication_failed"),
    ],
)
def test_authentication_reason_is_allowlisted(code: str | None, expected: str) -> None:
    observer = AuditObserver(case_id="AUTH-001", canaries={}, known_chunk_ids=())
    observer.finish(401, "failed", code)
    evidence = observer.snapshot()
    assert evidence.denial_code == expected
    assert "SyntheticSecretMustNotBeExported" not in evidence.model_dump_json()


def test_conflicting_denial_callbacks_are_incomplete() -> None:
    observer = AuditObserver(case_id="AUTH-001", canaries={}, known_chunk_ids=())
    observer.finish(401, "failed", "authentication_failed")
    observer.finish(401, "failed", "membership_inactive")
    assert observer.snapshot().terminal == "observer_failed"
