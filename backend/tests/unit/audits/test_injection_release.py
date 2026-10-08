from pathlib import Path
from typing import Literal

import pytest

from app.audits.contracts import Boundary, Terminal
from app.audits.injection_reports import (
    InjectionProviderRecord,
    build_injection_report,
    expected_injection_cases,
    validate_injection_release_directory,
    write_injection_report,
)
from app.audits.injection_scoring import score_injection
from tests.unit.audits.test_injection_reports import report


def publish_profile(
    directory: Path, mode: Literal["resistant", "obeying", "deny_all"]
) -> Path:
    original = report(fail=mode == "obeying")
    provider = InjectionProviderRecord(mode=mode)
    results = original.results
    if mode == "deny_all":
        cases = expected_injection_cases(original.documents, trials=1)
        results = tuple(
            score_injection(
                control,
                result.access_control.observation.model_copy(
                    update={
                        "terminal": Terminal.ABSTAINED,
                        "boundaries": tuple(
                            stage.model_copy(
                                update={"canary_matches": (), "chunk_ids": ()}
                            )
                            if stage.boundary
                            in {
                                Boundary.OUTPUT_CANDIDATE,
                                Boundary.OUTPUT_DELIVERED,
                                Boundary.CITATIONS_CANDIDATE,
                                Boundary.CITATIONS_DELIVERED,
                            }
                            else stage
                            for stage in result.access_control.observation.boundaries
                        ),
                    }
                ),
            )
            for control, result in zip(cases, results, strict=True)
        )
    artifact = build_injection_report(
        results,
        original.metadata.model_copy(update={"provider_id": provider.identifier}),
        documents=original.documents,
        provider=provider,
        trials=1,
    )
    return write_injection_report(artifact, directory)


def test_release_gate_checks_three_profiles_without_rewriting(tmp_path: Path) -> None:
    publish_profile(tmp_path, "resistant")
    publish_profile(tmp_path, "obeying")
    publish_profile(tmp_path, "deny_all")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    checked = validate_injection_release_directory(tmp_path)
    profiles = {artifact.provider.mode: artifact for artifact in checked}
    assert set(profiles) == {"resistant", "obeying", "deny_all"}
    assert profiles["resistant"].summary.exit_code == 0
    assert profiles["obeying"].summary.evaluated_successes == 3
    assert profiles["deny_all"].summary.evaluated_attacks == 0
    assert profiles["deny_all"].summary.baseline_failures == 3
    assert before == {path.name: path.read_bytes() for path in tmp_path.iterdir()}


@pytest.mark.parametrize(
    "fault", ["missing", "duplicate", "extra", "incomplete", "wrong"]
)
def test_release_gate_rejects_invalid_pack(tmp_path: Path, fault: str) -> None:
    publish_profile(tmp_path, "resistant")
    publish_profile(tmp_path, "obeying")
    if fault == "missing":
        pass
    elif fault == "duplicate":
        publish_profile(tmp_path, "resistant")
    elif fault == "incomplete":
        artifact = report(partial=True).model_copy(
            update={
                "provider": InjectionProviderRecord(mode="deny_all"),
                "metadata": report().metadata.model_copy(
                    update={"provider_id": "fixture-injection-deny_all-v1"}
                ),
            }
        )
        write_injection_report(artifact, tmp_path)
    elif fault == "wrong":
        artifact = report().model_copy(
            update={
                "provider": InjectionProviderRecord(mode="deny_all"),
                "metadata": report().metadata.model_copy(
                    update={"provider_id": "fixture-injection-deny_all-v1"}
                ),
            }
        )
        write_injection_report(artifact, tmp_path)
    else:
        publish_profile(tmp_path, "deny_all")
        (tmp_path / "extra.txt").write_text("extra", encoding="utf-8")
    with pytest.raises(ValueError, match="injection_artifact_invalid"):
        validate_injection_release_directory(tmp_path)
