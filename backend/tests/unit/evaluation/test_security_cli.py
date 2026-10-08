import json
from pathlib import Path

import pytest

from app.evaluation.security_reports import (
    build_injection_benchmark,
    write_injection_benchmark,
)
from tests.unit.audits.test_injection_reports import bindings
from tests.unit.evaluation.report_support import provenance
from tests.unit.evaluation.test_cli import unreachable
from tests.unit.evaluation.test_security_reports import observations


@pytest.mark.parametrize("partial", [False, True])
def test_offline_security_cli_replays_without_runtime(
    partial: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.evaluation import cli

    monkeypatch.setattr(cli, "_configuration", unreachable)
    monkeypatch.setattr(cli, "_run", unreachable)
    measured = build_injection_benchmark(
        provenance(),
        bindings(),
        observations(partial=partial),
        strategy="shared_pre_filter",
        collection_names=("ragelit_audit_test",),
        provider_profile="resistant",
    )
    path = write_injection_benchmark(measured, tmp_path)
    assert cli.main(["--validate-injection-benchmark", str(path)]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {
        "code": "injection_benchmark_artifact_valid",
        "exit_code": 0,
        "run_exit_code": 2 if partial else 0,
        "coverage_complete": not partial,
    }


@pytest.mark.parametrize("extra", [[], ["--provider", "fixture"]])
def test_invalid_security_artifact_or_mixed_mode_is_redacted(
    extra: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.evaluation import cli

    monkeypatch.setattr(cli, "_configuration", unreachable)
    assert (
        cli.main(
            [
                "--validate-injection-benchmark",
                str(tmp_path / "ExceptionSecretMarker.json"),
                *extra,
            ]
        )
        == 2
    )
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {
        "code": "utility_invalid_arguments"
        if extra
        else "utility_artifact_validation_failed",
        "exit_code": 2,
    }
    assert "ExceptionSecretMarker" not in output.out
