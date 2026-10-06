import json
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.evaluation.security_reports import validate_injection_benchmark
from tests.unit.audits.test_injection_reports import report
from tests.unit.evaluation.report_support import provenance
from tests.unit.evaluation.test_cli import unreachable
from tests.unit.evaluation.test_security_runner import (
    injection_workspace as injection_workspace,
)


@pytest.mark.parametrize(
    "arguments",
    [
        ["--pack", "utility", "--injection-profile", "resistant"],
        ["--pack", "injection", "--injection-trials", "0"],
        ["--pack", "injection", "--injection-trials", "21"],
        ["--pack", "injection", "--qdrant-container", "ignored"],
    ],
)
def test_invalid_injection_runtime_flags_never_reach_configuration(
    arguments: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.evaluation import cli

    monkeypatch.setattr(cli, "_configuration", unreachable)
    assert (
        cli.main(["--embedding-root", "missing", "--provider", "fixture", *arguments])
        == 2
    )
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {
        "code": "utility_invalid_arguments",
        "exit_code": 2,
    }


@pytest.mark.parametrize(
    "failure", ["none", "interrupted", "checkpoint", "preparation"]
)
def test_runtime_injection_cli_saves_original_observations_and_checkpoints(
    injection_workspace: SimpleNamespace,
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.audits import injection_fixtures, workspace
    from app.evaluation import cli, models, security_reports, security_runner
    from app.evaluation.security_runner import InjectionCapture

    fixture = injection_workspace
    config = fixture.workspace.config
    monkeypatch.setattr(cli, "_configuration", lambda: config)
    monkeypatch.setattr(cli, "collect_provenance", lambda *args: provenance())
    monkeypatch.setattr(
        models, "PinnedEmbeddingProvider", lambda root: fixture.workspace.embeddings
    )

    @contextmanager
    def owned(*args: Any, **kwargs: Any) -> Any:
        yield fixture.workspace

    monkeypatch.setattr(workspace, "AuditWorkspace", owned)

    def prepare(owned: Any, run_id: Any) -> Any:
        if failure == "preparation":
            raise OSError("ExceptionSecretMarker")
        return replace(fixture.pack, run_id=run_id)

    monkeypatch.setattr(injection_fixtures, "prepare_injection_pack", prepare)
    raw = tuple(result.access_control.observation for result in report().results)

    def capture(owned: Any, pack: Any, control: Any, **kwargs: Any) -> Any:
        index = next(i for i, case in enumerate(fixture.controls) if case == control)
        if index:
            checkpoints = [
                path
                for path in config.report_dir.rglob("*.json")
                if ".sha256." not in path.name
            ]
            assert checkpoints
            first = validate_injection_benchmark(checkpoints[0])
            assert first.provisional
            assert first.exit_code == 2
        return InjectionCapture(
            raw[index],
            failure == "interrupted" and index == 1,
            failure == "interrupted" and index == 1,
        )

    monkeypatch.setattr(security_runner, "capture_injection_query", capture)
    if failure == "checkpoint":
        original = security_reports.write_injection_benchmark

        def write(item: Any, directory: Path) -> Path:
            if item.provisional:
                raise OSError("ExceptionSecretMarker")
            return original(item, directory)

        monkeypatch.setattr(security_reports, "write_injection_benchmark", write)
    result_code = cli.main(
        [
            "--embedding-root",
            str(config.root),
            "--provider",
            "fixture",
            "--pack",
            "injection",
            "--injection-profile",
            "resistant",
        ]
    )
    output = capsys.readouterr()
    assert output.err == ""
    assert "ExceptionSecretMarker" not in output.out
    result = json.loads(output.out)
    assert result_code == (0 if failure == "none" else 2)
    expected = {"none": 6, "interrupted": 2, "checkpoint": 1, "preparation": 0}[failure]
    assert result["case_count"] == expected
    final = validate_injection_benchmark(
        config.report_dir / "injection-benchmarks" / (result["run_id"] + ".json")
    )
    assert len(final.observations) == expected
    assert not final.provisional
    assert final.runtime_failed == (failure != "none")
    assert final.coverage_complete == (failure == "none")
    assert final.exit_code == result_code
    assert final.provenance.generation.mode == "fixture"
    assert final.provider_profile == "resistant"
    snapshots = [
        path
        for path in (config.report_dir / "injection-checkpoints").rglob("*.json")
        if ".sha256." not in path.name
    ]
    assert len(snapshots) == (expected if failure in {"none", "interrupted"} else 0)
    if failure == "interrupted":
        final_snapshot = max(
            (validate_injection_benchmark(path) for path in snapshots),
            key=lambda item: len(item.observations),
        )
        assert final_snapshot.runtime_failed
        assert len(final_snapshot.observations) == 2
