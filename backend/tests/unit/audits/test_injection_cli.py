import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from app.audits.contracts import AuditCase, AuditObservation, Boundary
from app.audits.injection_cli import execute_injection_cases
from app.audits.injection_reports import (
    build_injection_report,
    expected_injection_cases,
)
from app.audits.target import BundledAuditTarget
from tests.unit.audits.test_injection_release import publish_profile
from tests.unit.audits.test_injection_reports import report


def invoke(
    arguments: list[str], values: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("RAGELIT_AUDIT_")
    }
    environment.update(values or {})
    return subprocess.run(
        [sys.executable, "-m", "app.audits.injection_cli", *arguments],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60 if os.name == "nt" else 30,
        check=False,
    )


@pytest.mark.parametrize(
    "arguments",
    [
        ["--unknown", "SensitiveInputNeverEcho"],
        ["--profile", "SensitiveInputNeverEcho"],
        ["--trials", "0"],
        ["--trials", "21"],
        ["--trials", "SensitiveInputNeverEcho"],
        ["--profile", "local"],
        ["--model", "SensitiveInputNeverEcho"],
        [
            "--profile",
            "local",
            "--local-base-url",
            "https://SensitiveInputNeverEcho/v1",
            "--model",
            "local-model",
            "--weights-sha256",
            "a" * 64,
        ],
        ["--validate-reports", ".", "--profile", "obeying"],
        ["--validate-reports", ".", "--trials", "2"],
        ["--validate-report", ".", "--validate-reports", "."],
    ],
)
def test_invalid_options_are_redacted_before_workspace_access(
    arguments: list[str],
) -> None:
    process = invoke(arguments)
    assert process.returncode == 2
    assert json.loads(process.stdout) == {
        "code": "injection_invalid_arguments",
        "exit_code": 2,
    }
    assert process.stderr == ""
    assert "SensitiveInputNeverEcho" not in process.stdout


def test_help_needs_no_dependencies_or_services() -> None:
    process = invoke(["--help"])
    assert process.returncode == 0
    assert "--trials" in process.stdout
    assert "--local-base-url" in process.stdout
    assert process.stderr == ""


def test_ordinary_provider_configuration_is_not_used() -> None:
    process = invoke([], {"RAGELIT_LLM_API_KEY": "SensitiveInputNeverEcho"})
    assert process.returncode == 2
    assert json.loads(process.stdout) == {
        "code": "injection_invalid_configuration",
        "exit_code": 2,
    }
    assert process.stderr == ""


def test_single_and_release_validation_need_no_workspace(tmp_path: Path) -> None:
    path = publish_profile(tmp_path, "resistant")
    single = invoke(["--validate-report", str(path)])
    assert single.returncode == 0
    assert json.loads(single.stdout) == {
        "code": "injection_artifacts_valid",
        "exit_code": 0,
    }
    publish_profile(tmp_path, "obeying")
    publish_profile(tmp_path, "deny_all")
    release = invoke(["--validate-reports", str(tmp_path)])
    assert release.returncode == 0
    assert json.loads(release.stdout) == {
        "code": "injection_artifacts_valid",
        "exit_code": 0,
    }
    assert single.stderr == release.stderr == ""


def test_validation_failure_does_not_echo_the_path(tmp_path: Path) -> None:
    process = invoke(["--validate-report", str(tmp_path / "SensitiveInputNeverEcho")])
    assert process.returncode == 2
    assert json.loads(process.stdout) == {
        "code": "injection_artifact_validation_failed",
        "exit_code": 2,
    }
    assert process.stderr == ""


@pytest.mark.parametrize(
    "stale,interrupted", [(False, False), (False, True), (True, False)]
)
def test_execution_failure_preserves_only_matching_boundary_evidence(
    stale: bool,
    interrupted: bool,
) -> None:
    artifact = report(fail=True)
    cases = expected_injection_cases(artifact.documents, trials=1)
    observations = tuple(
        result.access_control.observation for result in artifact.results
    )

    class InterruptedTarget:
        last_observation: AuditObservation | None = None
        position = 0

        def execute(self, control: AuditCase) -> AuditObservation:
            assert control.id == cases[self.position].access_case.id
            observed = observations[self.position]
            if self.position == 1:
                if not stale:
                    self.last_observation = observed.model_copy(
                        update={"boundaries": observed.boundaries[:-1]}
                    )
                if interrupted:
                    raise KeyboardInterrupt
                raise RuntimeError("SensitiveInputNeverEcho")
            self.last_observation = observed
            self.position += 1
            return observed

    results, failed = execute_injection_cases(
        cast(BundledAuditTarget, InterruptedTarget()), cases
    )
    assert failed
    assert len(results) == (1 if stale else 2)
    partial = build_injection_report(
        results,
        artifact.metadata,
        documents=artifact.documents,
        provider=artifact.provider,
        trials=1,
        runtime_failed=failed,
    )
    assert partial.summary.exit_code == 2
    assert not partial.summary.coverage_complete
    assert partial.summary.observed_signals == (0 if stale else 1)
    if not stale:
        assert results[1].first_instruction_boundary == Boundary.OUTPUT_CANDIDATE
        assert not results[1].coverage_complete
    assert "SensitiveInputNeverEcho" not in partial.model_dump_json()
