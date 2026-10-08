import json
from contextlib import contextmanager
from typing import Any

import pytest

from app.evaluation.access_reports import (
    build_access_benchmark,
    validate_access_benchmark,
    write_access_benchmark,
)
from tests.unit.evaluation.report_support import provenance
from tests.unit.evaluation.test_access_registry import RUN_ID
from tests.unit.evaluation.test_access_reports import records
from tests.unit.evaluation.test_access_runner import (
    access_workspace as access_workspace,
)
from tests.unit.evaluation.test_cli import unreachable


@pytest.mark.parametrize(
    "arguments",
    [
        [
            "--pack",
            "access-control",
            "--embedding-root",
            "missing",
            "--provider",
            "fixture",
            "--cohort-id",
            "ExceptionSecretMarker",
        ],
        [
            "--embedding-root",
            "missing",
            "--provider",
            "fixture",
            "--cohort-id",
            str(RUN_ID),
        ],
        [
            "--pack",
            "injection",
            "--embedding-root",
            "missing",
            "--provider",
            "fixture",
            "--cohort-id",
            str(RUN_ID),
        ],
        ["--validate-access-benchmark", "missing", "--cohort-id", str(RUN_ID)],
        ["--compare-access-benchmarks", "one", "two", "--cohort-id", str(RUN_ID)],
    ],
)
def test_cohort_option_rejects_invalid_uuid_or_unrelated_modes_before_configuration(
    arguments: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.evaluation import cli

    monkeypatch.setattr(cli, "_configuration", unreachable)
    assert cli.main(arguments) == 2
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {
        "code": "utility_invalid_arguments",
        "exit_code": 2,
    }
    assert "ExceptionSecretMarker" not in output.out


@pytest.mark.parametrize("existing", ["fresh", "instance", "artifact", "checkpoint"])
def test_access_cohort_selects_matching_inputs_but_never_reuses_owned_state(
    existing: str,
    access_workspace: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.audits import target, workspace
    from app.evaluation import access_runner, cli, models
    from app.evaluation.access_runner import AccessCapture

    fixture = access_workspace
    config = fixture.workspace.config
    prepared_inventory = fixture.workspace.bindings
    before_preparation = prepared_inventory.model_copy(update={"instances": {}})
    fixture.workspace.bindings = (
        prepared_inventory if existing == "instance" else before_preparation
    )
    monkeypatch.setattr(cli, "_configuration", lambda: config)
    monkeypatch.setattr(cli, "uuid4", unreachable)
    monkeypatch.setattr(
        cli,
        "collect_provenance",
        (lambda *args: provenance()) if existing == "fresh" else unreachable,
    )
    monkeypatch.setattr(
        models, "PinnedEmbeddingProvider", lambda root: fixture.workspace.embeddings
    )

    @contextmanager
    def owned(*args: Any, **kwargs: Any) -> Any:
        yield fixture.workspace

    monkeypatch.setattr(workspace, "AuditWorkspace", owned)

    def prepare(owned: Any, run_id: Any) -> Any:
        assert run_id == RUN_ID
        fixture.workspace.bindings = prepared_inventory
        return fixture.pack

    monkeypatch.setattr(
        target, "prepare_pack", prepare if existing == "fresh" else unreachable
    )
    raw = records()[0]
    monkeypatch.setattr(
        access_runner,
        "capture_access_query",
        lambda *args, **kwargs: AccessCapture(raw, False, True),
    )
    if existing == "artifact":
        write_access_benchmark(
            build_access_benchmark(
                provenance(),
                prepared_inventory,
                records(),
                strategy="shared_pre_filter",
                collection_names=(config.name,),
                run_id=RUN_ID,
            ),
            config.report_dir / "access-benchmarks",
        )
    elif existing == "checkpoint":
        (config.report_dir / "access-checkpoints" / RUN_ID.hex).mkdir(parents=True)
    original_inventory = fixture.workspace.bindings.model_dump_json()
    original_files = {
        path: path.read_bytes()
        for path in config.report_dir.rglob("*")
        if path.is_file()
    }
    assert (
        cli.main(
            [
                "--pack",
                "access-control",
                "--embedding-root",
                str(config.root),
                "--provider",
                "fixture",
                "--cohort-id",
                str(RUN_ID),
            ]
        )
        == 2
    )
    output = capsys.readouterr()
    assert output.err == ""
    assert "ExceptionSecretMarker" not in output.out
    parsed = json.loads(output.out)
    if existing != "fresh":
        assert parsed == {
            "code": "access_benchmark_cohort_already_used",
            "exit_code": 2,
        }
        assert fixture.workspace.bindings.model_dump_json() == original_inventory
        assert {
            path: path.read_bytes()
            for path in config.report_dir.rglob("*")
            if path.is_file()
        } == original_files
    else:
        assert parsed["code"] == "access_benchmark_runtime_failed"
        assert parsed["run_id"] == str(RUN_ID)
        assert parsed["case_count"] == 1
        report = validate_access_benchmark(
            config.report_dir / "access-benchmarks" / f"{RUN_ID}.json"
        )
        assert report.cohort_id == RUN_ID
        assert report.runtime_failed and not report.coverage_complete
        assert len(report.records) == 1
        assert report.inventory == prepared_inventory
