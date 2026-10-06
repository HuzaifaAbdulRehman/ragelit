import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from app.evaluation.access_reports import validate_access_benchmark
from tests.unit.evaluation.report_support import provenance
from tests.unit.evaluation.test_access_registry import RUN_ID
from tests.unit.evaluation.test_access_reports import records
from tests.unit.evaluation.test_access_runner import (
    access_workspace as access_workspace,
)
from tests.unit.evaluation.test_cli import unreachable


@pytest.mark.parametrize(
    "extra",
    [
        ["--injection-profile", "resistant"],
        ["--injection-trials", "1"],
        ["--qdrant-container", "unused"],
    ],
)
def test_access_runtime_rejects_unrelated_options_before_configuration(
    extra: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.evaluation import cli

    monkeypatch.setattr(cli, "_configuration", unreachable)
    assert (
        cli.main(
            [
                "--pack",
                "access-control",
                "--embedding-root",
                "missing",
                "--provider",
                "fixture",
                *extra,
            ]
        )
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
def test_access_runtime_publishes_original_records_and_durable_snapshots(
    access_workspace: Any,
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.audits import target, workspace
    from app.evaluation import access_reports, access_runner, cli, models
    from app.evaluation.access_runner import AccessCapture

    fixture = access_workspace
    config = fixture.workspace.config
    monkeypatch.setattr(cli, "_configuration", lambda: config)
    monkeypatch.setattr(cli, "collect_provenance", lambda *args: provenance())
    monkeypatch.setattr(cli, "uuid4", lambda: RUN_ID)
    monkeypatch.setattr(
        models, "PinnedEmbeddingProvider", lambda root: fixture.workspace.embeddings
    )

    @contextmanager
    def owned(*args: Any, **kwargs: Any) -> Any:
        yield fixture.workspace

    monkeypatch.setattr(workspace, "AuditWorkspace", owned)

    def prepare(owned: Any, run_id: Any) -> Any:
        assert run_id == RUN_ID
        if failure == "preparation":
            raise OSError("ExceptionSecretMarker")
        return fixture.pack

    monkeypatch.setattr(target, "prepare_pack", prepare)
    raw = records()

    def capture(owned: Any, pack: Any, case: Any, **kwargs: Any) -> Any:
        index = next(
            index
            for index, item in enumerate(raw)
            if item.observation.case_id == case.id
        )
        if index:
            paths = [
                path
                for path in config.report_dir.rglob("*.json")
                if ".sha256." not in path.name
            ]
            assert paths
            prior = validate_access_benchmark(paths[0])
            assert prior.provisional and prior.exit_code == 2
            assert prior.cohort_id == RUN_ID
        return AccessCapture(
            raw[index],
            failure == "interrupted" and index == 1,
            failure == "interrupted" and index == 1,
        )

    monkeypatch.setattr(access_runner, "capture_access_query", capture)
    if failure == "checkpoint":
        original = access_reports.write_access_benchmark

        def write(item: Any, directory: Path) -> Path:
            if item.provisional:
                raise OSError("ExceptionSecretMarker")
            return original(item, directory)

        monkeypatch.setattr(access_reports, "write_access_benchmark", write)
    code = cli.main(
        [
            "--pack",
            "access-control",
            "--embedding-root",
            str(config.root),
            "--provider",
            "fixture",
        ]
    )
    output = capsys.readouterr()
    assert output.err == ""
    assert "ExceptionSecretMarker" not in output.out
    parsed = json.loads(output.out)
    assert code == (0 if failure == "none" else 2)
    expected = {"none": 51, "interrupted": 2, "checkpoint": 1, "preparation": 0}[
        failure
    ]
    assert parsed["case_count"] == expected
    final = validate_access_benchmark(
        config.report_dir / "access-benchmarks" / (parsed["run_id"] + ".json")
    )
    assert len(final.records) == expected
    assert final.cohort_id == RUN_ID
    assert not final.provisional
    assert final.coverage_complete == (failure == "none")
    assert final.runtime_failed == (failure != "none")
    assert final.provenance.generation.mode == "fixture"
    snapshots = [
        path
        for path in (config.report_dir / "access-checkpoints").rglob("*.json")
        if ".sha256." not in path.name
    ]
    assert len(snapshots) == (expected if failure in {"none", "interrupted"} else 0)
    if failure == "interrupted":
        last = max(
            (validate_access_benchmark(path) for path in snapshots),
            key=lambda item: len(item.records),
        )
        assert last.runtime_failed and len(last.records) == 2
