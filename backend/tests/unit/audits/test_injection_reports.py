import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid5

import pytest
from pydantic import ValidationError

from app.audits.contracts import (
    AuditObservation,
    Boundary,
    BoundaryEvidence,
    CanaryMatch,
    Terminal,
)
from app.audits.injection_fixtures import generate_injection_fixtures
from app.audits.injection_reports import (
    InjectionProviderRecord,
    InjectionReport,
    build_injection_report,
    expected_injection_cases,
    validate_injection_report,
    write_injection_report,
)
from app.audits.injection_scoring import score_injection
from app.audits.workspace import DocumentBinding
from tests.unit.audits.support import metadata


def bindings() -> dict[str, DocumentBinding]:
    return {
        document.id: DocumentBinding(
            document_id=UUID(int=index + 10),
            version_id=UUID(int=index + 100),
            chunk_ids=(uuid5(UUID(int=index + 100), "0"),),
            content_hash=hashlib.sha256(document.text.encode()).hexdigest(),
        )
        for index, document in enumerate(generate_injection_fixtures().documents)
    }


def report(
    *, fail: bool = False, partial: bool = False, runtime: bool = False
) -> InjectionReport:
    documents = bindings()
    template = generate_injection_fixtures()
    cases = expected_injection_cases(documents, trials=1)
    results = []
    for control in cases:
        stages = []
        for index, boundary in enumerate(Boundary):
            matches: tuple[CanaryMatch, ...] = ()
            chunks: tuple[UUID, ...] = ()
            if boundary in {
                Boundary.RETRIEVAL_RAW,
                Boundary.RETRIEVAL_ACCEPTED,
                Boundary.CONTEXT,
                Boundary.CITATIONS_CANDIDATE,
                Boundary.CITATIONS_DELIVERED,
            }:
                chunks = control.access_case.required_chunks
            if boundary == Boundary.CONTEXT and control.attack_id:
                matches = (CanaryMatch(canary_id=control.attack_id),)
            if boundary in {Boundary.OUTPUT_CANDIDATE, Boundary.OUTPUT_DELIVERED}:
                matches = (CanaryMatch(canary_id=control.fact_id),)
                if fail and control.attack_id:
                    matches += (CanaryMatch(canary_id=control.attack_id),)
            stages.append(
                BoundaryEvidence(
                    boundary=boundary,
                    sequence=index,
                    chunk_ids=chunks,
                    canary_matches=matches,
                )
            )
        observed = AuditObservation(
            case_id=control.access_case.id,
            http_status=200,
            terminal=Terminal.ANSWERED,
            scope_hash="1" * 64,
            boundaries=tuple(stages),
        )
        results.append(score_injection(control, observed))
    run_metadata = metadata().model_copy(
        update={
            "profile": "safe",
            "pack_id": "injection-v1",
            "generator_id": "synthetic-injection-v1",
            "embedding_id": "fixture-topic-v1",
            "provider_id": "fixture-injection-resistant-v1",
            "template_hash": template.checksum,
        }
    )
    return build_injection_report(
        tuple(results[:1] if partial else results),
        run_metadata,
        documents=documents,
        provider=InjectionProviderRecord(mode="resistant"),
        trials=1,
        runtime_failed=runtime,
    )


def rewrite(path: Path, payload: dict[str, object]) -> None:
    content = json.dumps(payload).encode()
    path.write_bytes(content)
    path.with_suffix(".sha256.json").write_text(
        json.dumps(
            {
                "filename": path.name,
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        ),
        encoding="utf-8",
    )


def test_report_is_redacted_original_bytes_with_replayed_counts(tmp_path: Path) -> None:
    artifact = report()
    path = write_injection_report(artifact, tmp_path)
    checked = validate_injection_report(path)
    assert checked == artifact
    assert checked.summary.exit_code == 0
    assert checked.summary.attempted_attacks == 3
    assert checked.summary.evaluated_attacks == 3
    assert checked.summary.attack_success_rate == 0.0
    assert checked.summary.benign_controls_passed
    assert len(checked.results) == 6
    assert checked.required_case_ids == (
        "org-1:benign:trial-1",
        "org-1:attack:trial-1",
        "org-2:benign:trial-1",
        "org-2:attack:trial-1",
        "org-3:benign:trial-1",
        "org-3:attack:trial-1",
    )
    original = path.read_bytes()
    assert original == (artifact.model_dump_json(indent=2) + "\n").encode()
    for marker in (
        b"FACTANSWER",
        b"AUDITCANARY",
        b"Bearer ",
        b'"answer"',
        b'"text"',
        b'"password"',
    ):
        assert marker not in original


def test_confirmed_attacks_and_incomplete_inventory_stay_visible(
    tmp_path: Path,
) -> None:
    attacked = report(fail=True)
    assert attacked.summary.exit_code == 1
    assert attacked.summary.evaluated_successes == 3
    incomplete = report(partial=True, runtime=True)
    checked = validate_injection_report(write_injection_report(incomplete, tmp_path))
    assert checked.summary.exit_code == 2
    assert checked.summary.incomplete_cases == 5
    assert checked.runtime_failed
    assert checked.summary.attack_success_rate is None


def test_writer_never_replaces_an_existing_artifact(tmp_path: Path) -> None:
    artifact = report()
    path = write_injection_report(artifact, tmp_path)
    original = path.read_bytes()
    receipt = path.with_suffix(".sha256.json").read_bytes()
    with pytest.raises(FileExistsError):
        write_injection_report(artifact, tmp_path)
    assert path.read_bytes() == original
    assert path.with_suffix(".sha256.json").read_bytes() == receipt


@pytest.mark.parametrize(
    "change",
    [
        "summary",
        "result",
        "required_ids",
        "bindings",
        "binding_hash",
        "model_id",
        "template_hash",
        "raw_field",
        "raw_marker",
        "unknown_marker",
        "run_id",
        "unknown_result",
        "duplicate_result",
    ],
)
def test_validator_rejects_rechecksummed_tampering(tmp_path: Path, change: str) -> None:
    path = write_injection_report(report(), tmp_path)
    payload = json.loads(path.read_bytes())
    if change == "summary":
        payload["summary"]["evaluated_attacks"] = 0
    elif change == "result":
        payload["results"][0]["status"] = "fail"
    elif change == "required_ids":
        payload["required_case_ids"] = ["org-1:benign:trial-1"]
    elif change == "bindings":
        payload["documents"]["org-1-organization"]["chunk_ids"] = [str(UUID(int=999))]
    elif change == "binding_hash":
        payload["documents_hash"] = "0" * 64
    elif change == "model_id":
        payload["metadata"]["provider_id"] = "a-different-provider"
    elif change == "template_hash":
        payload["metadata"]["template_hash"] = "0" * 64
    elif change == "raw_field":
        payload["answer"] = "never export raw answers"
    elif change == "raw_marker":
        payload["provider"]["local"] = {
            "base_url": "http://127.0.0.1:8001/v1",
            "model": "FACTANSWERsecret",
            "weights_hash": "a" * 64,
        }
    elif change == "unknown_marker":
        payload["results"][0]["access_control"]["observation"]["boundaries"][0][
            "canary_matches"
        ] = [{"canary_id": "unknown-label"}]
    elif change == "run_id":
        payload["run_id"] = str(UUID(int=999))
    elif change == "unknown_result":
        payload["results"][0]["case_id"] = "unknown"
    else:
        payload["results"].append(payload["results"][0])
    rewrite(path, payload)
    with pytest.raises((ValueError, ValidationError)):
        validate_injection_report(path)


def test_receipt_checks_exact_original_bytes(tmp_path: Path) -> None:
    path = write_injection_report(report(), tmp_path)
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError):
        validate_injection_report(path)


def test_provider_record_cannot_mix_local_and_fake_modes() -> None:
    with pytest.raises(ValidationError):
        InjectionProviderRecord(mode="local")
    with pytest.raises(ValidationError):
        InjectionProviderRecord.model_validate(
            {
                "mode": "resistant",
                "local": {
                    "base_url": "http://127.0.0.1:8001/v1",
                    "model": "local-model",
                    "weights_hash": "a" * 64,
                },
            }
        )


def test_document_bindings_must_match_the_owned_fixture_content() -> None:
    documents = bindings()
    documents["org-1-organization"] = documents["org-1-organization"].model_copy(
        update={"content_hash": "0" * 64}
    )
    with pytest.raises(ValueError, match="injection_artifact_invalid"):
        expected_injection_cases(documents, trials=1)
