import json
from pathlib import Path

import pytest

from app.evaluation.access_reports import write_access_benchmark
from tests.unit.evaluation.test_access_reports import benchmark
from tests.unit.evaluation.test_cli import unreachable


@pytest.mark.parametrize("provisional", [False, True])
def test_access_cli_replays_without_models_or_services(
    provisional: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.evaluation import cli

    monkeypatch.setattr(cli, "_configuration", unreachable)
    monkeypatch.setattr(cli, "_run", unreachable)
    monkeypatch.setattr(cli, "_run_injection", unreachable)
    path = write_access_benchmark(benchmark(provisional=provisional), tmp_path)
    assert cli.main(["--validate-access-benchmark", str(path)]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {
        "code": "access_benchmark_artifact_valid",
        "exit_code": 0,
        "run_exit_code": 2 if provisional else 0,
        "coverage_complete": not provisional,
    }


@pytest.mark.parametrize(
    "extra", [[], ["--provider", "fixture"], ["--pack", "injection"]]
)
def test_invalid_access_cli_artifact_or_mixed_mode_is_redacted(
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
                "--validate-access-benchmark",
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
