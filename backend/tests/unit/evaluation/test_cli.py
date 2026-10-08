import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from app.evaluation.reports import write_utility_report
from tests.unit.evaluation.report_support import report


def unreachable(*args: Any, **kwargs: Any) -> None:
    raise RuntimeError("ExceptionSecretMarker")


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--unknown", "ExceptionSecretMarker"],
        ["--embedding-root", "missing", "--provider", "unknown"],
        ["--embedding-root", "missing", "--provider", "fixture", "--lab"],
        [
            "--embedding-root",
            "missing",
            "--provider",
            "fixture",
            "--strategy",
            "lab_post_filter",
        ],
        ["--embedding-root", "missing", "--provider", "local"],
        ["--embedding-root", "missing", "--provider", "fixture", "--model", "secret"],
        ["--validate-report", "missing", "--provider", "fixture"],
        ["--validate-report", "missing", "--compare-reports", "one", "two"],
    ],
)
def test_argument_errors_do_not_reach_configuration_or_echo_values(
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


def test_configuration_failure_is_redacted(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.evaluation import cli

    monkeypatch.setattr(cli, "_configuration", unreachable)
    assert cli.main(["--embedding-root", "missing", "--provider", "fixture"]) == 2
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {
        "code": "utility_invalid_configuration",
        "exit_code": 2,
    }
    assert "ExceptionSecretMarker" not in output.out


@pytest.mark.parametrize("partial", [False, True])
def test_offline_validation_keeps_integrity_and_run_gates_separate(
    tmp_path: Path,
    partial: bool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.evaluation import cli

    monkeypatch.setattr(cli, "_configuration", unreachable)
    path = write_utility_report(report(partial=partial), tmp_path)
    assert cli.main(["--validate-report", str(path)]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {
        "code": "utility_artifact_valid",
        "exit_code": 0,
        "run_exit_code": 2 if partial else 0,
        "coverage_complete": not partial,
    }


def test_offline_comparison_does_not_load_models_or_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.evaluation import cli

    monkeypatch.setattr(cli, "_configuration", unreachable)
    first = write_utility_report(report(), tmp_path)
    second = write_utility_report(report(strategy="tenant_collections"), tmp_path)
    assert cli.main(["--compare-reports", str(first), str(second)]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    result = json.loads(output.out)
    assert result["code"] == "utility_comparison_complete"
    assert result["exit_code"] == 0
    assert result["comparison"]["recall"]["mean_difference"] == 0.0
    assert result["comparison"]["recall"]["paired_queries"] == 87


def test_bad_artifact_diagnostic_does_not_echo_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.evaluation import cli

    assert (
        cli.main(["--validate-report", str(tmp_path / "ExceptionSecretMarker.json")])
        == 2
    )
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {
        "code": "utility_artifact_validation_failed",
        "exit_code": 2,
    }
    assert "ExceptionSecretMarker" not in output.out


def test_external_generation_url_is_rejected_before_configuration(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.evaluation import cli

    monkeypatch.setattr(cli, "_configuration", unreachable)
    assert (
        cli.main(
            [
                "--embedding-root",
                "missing",
                "--provider",
                "local",
                "--base-url",
                "https://example.com/v1",
                "--model",
                "test-model",
                "--weights-sha256",
                "a" * 64,
                "--server-version",
                "server-1",
            ]
        )
        == 2
    )
    output = capsys.readouterr()
    assert json.loads(output.out) == {
        "code": "utility_invalid_arguments",
        "exit_code": 2,
    }


def test_machine_record_uses_real_host_capacity_without_hostname() -> None:
    from app.evaluation.cli import machine_record

    result = machine_record()
    assert result.logical_cpus >= 1
    assert result.memory_bytes > 0
    assert result.python_version
    assert "hostname" not in result.model_dump()


@pytest.mark.parametrize("checkpoint_fails", [False, True])
@pytest.mark.parametrize("first_query_fails", [False, True])
def test_run_preserves_completed_records_and_durable_provisional_snapshots(
    tmp_path: Path,
    checkpoint_fails: bool,
    first_query_fails: bool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from contextlib import contextmanager
    from types import SimpleNamespace

    from app.audits import seeding, workspace
    from app.evaluation import cli, models, runner
    from app.evaluation.reports import validate_utility_report
    from app.evaluation.runner import QueryCapture, execute_query_cohort
    from tests.unit.evaluation.report_support import bindings, provenance, record

    config = workspace.AuditConfiguration(
        environment="test",
        database_admin_url=SecretStr(
            "postgresql+psycopg://postgres:test@127.0.0.1:5432/ragelit_audit_cli"
        ),
        qdrant_url="http://127.0.0.1:6333",
        root=tmp_path / "ragelit_audit_cli",
        application_password=SecretStr("test-only-cli-application"),
        fixture_password=SecretStr("test-only-cli-fixture"),
    )
    monkeypatch.setattr(cli, "_configuration", lambda: config)
    monkeypatch.setattr(
        models,
        "PinnedEmbeddingProvider",
        lambda root: SimpleNamespace(
            fingerprint=provenance().embedding_fingerprint, dimension=384
        ),
    )
    monkeypatch.setattr(cli, "collect_provenance", lambda *args: provenance())
    opened: list[Any] = []

    @contextmanager
    def owned(config: Any, template: Any, *, embeddings: Any) -> Any:
        assert config.embedding_dimension == 384
        assert config.embedding_fingerprint == embeddings.fingerprint
        assert template.pack_id == "utility-v1"
        opened.append(config)
        yield SimpleNamespace(
            bindings=SimpleNamespace(seeded=False),
            store=SimpleNamespace(collection_names=lambda: ("ragelit_audit_test",)),
        )

    monkeypatch.setattr(workspace, "AuditWorkspace", owned)
    monkeypatch.setattr(seeding, "seed_workspace", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "utility_document_bindings", lambda *args: bindings())
    completed: list[str] = []
    observed_failure_flags: list[bool] = []

    def execute(workspace: Any, corpus: Any, generation: Any, **kwargs: Any) -> Any:
        assert generation.mode == "fixture"
        assert kwargs["strategy"] == "shared_pre_filter"
        assert kwargs["lab"] is False

        def capture(query: Any) -> QueryCapture:
            if completed:
                snapshots = tuple(
                    (config.report_dir / "utility-checkpoints").rglob("*.json")
                )
                if not checkpoint_fails:
                    artifacts = [p for p in snapshots if ".sha256." not in p.name]
                    assert len(artifacts) == 1
                    first = validate_utility_report(artifacts[0])
                    assert first.provisional
                    observed_failure_flags.append(first.runtime_failed)
                    assert first.exit_code == 2
                    assert len(first.records) == 1
            index = len(completed)
            completed.append(query.id)
            item = record(index)
            if first_query_fails and index == 0:
                from app.audits.contracts import Boundary, Terminal

                stages = tuple(
                    stage.model_copy(update={"state": "unobserved", "chunk_ids": ()})
                    if stage.boundary
                    not in {
                        Boundary.RETRIEVAL_RAW,
                        Boundary.RETRIEVAL_ACCEPTED,
                        Boundary.CONTEXT,
                    }
                    else stage
                    for stage in item.observation.boundaries
                )
                item = item.model_copy(
                    update={
                        "observation": item.observation.model_copy(
                            update={
                                "http_status": 504,
                                "terminal": Terminal.RUNTIME_FAILED,
                                "boundaries": stages,
                            }
                        ),
                        "citations": (),
                        "answer_label_match": None,
                        "error_code": "generation_timeout",
                    }
                )
            return QueryCapture(item, index == 1)

        return execute_query_cohort(corpus, capture, on_record=kwargs["on_record"])

    monkeypatch.setattr(runner, "execute_utility_queries", execute)
    if checkpoint_fails:
        from app.evaluation import reports

        original_write = reports.write_utility_report

        def write(item: Any, directory: Path) -> Path:
            if item.provisional:
                raise OSError("ExceptionSecretMarker")
            return original_write(item, directory)

        monkeypatch.setattr(reports, "write_utility_report", write)
    assert cli.main(["--embedding-root", str(tmp_path), "--provider", "fixture"]) == 2
    output = capsys.readouterr()
    assert output.err == ""
    result = json.loads(output.out)
    assert result["code"] == "utility_runtime_failed"
    assert result["provider_mode"] == "fixture"
    assert result["query_count"] == (1 if checkpoint_fails else 2)
    assert observed_failure_flags == ([] if checkpoint_fails else [first_query_fails])
    assert "ExceptionSecretMarker" not in output.out
    assert len(opened) == 1
    finals = tuple(config.report_dir.glob("*.json"))
    final = validate_utility_report(
        next(path for path in finals if ".sha256." not in path.name)
    )
    assert final.runtime_failed
    assert not final.provisional
    assert not final.inventory_complete
    assert len(final.records) == (1 if checkpoint_fails else 2)
    assert final.records[0].query_id == "org-1:change-notice"
    assert final.exit_code == 2
