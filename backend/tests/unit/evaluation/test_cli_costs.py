import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import SecretStr

from app.evaluation.cost_reports import (
    build_cost_report,
    validate_cost_report,
    write_cost_report,
)
from tests.unit.evaluation.report_support import bindings, provenance, record, report
from tests.unit.evaluation.test_cli import unreachable
from tests.unit.evaluation.test_cost_reports import cost_inputs


def test_offline_cost_validation_does_not_load_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.evaluation import cli

    monkeypatch.setattr(cli, "_configuration", unreachable)
    item = build_cost_report(
        provenance(),
        strategy="shared_pre_filter",
        collection_names=("ragelit_audit_test",),
        utility=report(partial=True),
        **cost_inputs(),
    )
    path = write_cost_report(item, tmp_path)
    assert cli.main(["--validate-cost-report", str(path)]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {
        "code": "utility_cost_artifact_valid",
        "exit_code": 0,
        "run_exit_code": 2,
        "coverage_complete": False,
    }


@pytest.mark.parametrize("extra", [[], ["--qdrant-container", "ExceptionSecretMarker"]])
def test_offline_cost_failure_is_redacted(
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
                "--validate-cost-report",
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


@pytest.mark.parametrize(
    "failure", ["none", "index", "storage", "revocation", "interrupted", "reused"]
)
def test_cli_preserves_cost_evidence_and_missing_denominators(
    failure: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.audits import seeding, workspace
    from app.evaluation import cli, costs, models, revocations, runner
    from app.evaluation.dataset import generate_utility_corpus
    from app.evaluation.reports import validate_utility_report
    from app.evaluation.runner import UtilityExecution

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
    monkeypatch.setattr(cli, "collect_provenance", lambda *args: provenance())
    monkeypatch.setattr(
        models,
        "PinnedEmbeddingProvider",
        lambda root: SimpleNamespace(
            fingerprint=provenance().embedding_fingerprint,
            dimension=384,
        ),
    )

    @contextmanager
    def owned(*args: Any, **kwargs: Any) -> Any:
        yield SimpleNamespace(
            bindings=SimpleNamespace(seeded=failure == "reused"),
            store=SimpleNamespace(collection_names=lambda: ("ragelit_audit_test",)),
        )

    monkeypatch.setattr(workspace, "AuditWorkspace", owned)

    def seed(owned: Any, template: Any, *, on_ingestion: Any) -> None:
        if failure == "reused":
            return
        for doc in generate_utility_corpus().documents:
            on_ingestion(doc.id, 2.0, failure != "index")
            if failure == "index":
                raise RuntimeError("ExceptionSecretMarker")

    monkeypatch.setattr(seeding, "seed_workspace", seed)
    monkeypatch.setattr(runner, "utility_document_bindings", lambda *args: bindings())

    def storage(*args: Any, **kwargs: Any) -> Any:
        if failure == "storage":
            raise OSError("ExceptionSecretMarker")
        return cost_inputs()["storage"]

    monkeypatch.setattr(costs, "collect_storage", storage)

    def execute(*args: Any, **kwargs: Any) -> UtilityExecution:
        records = (
            (record(0),)
            if failure == "interrupted"
            else tuple(record(i) for i in range(87))
        )
        kwargs["on_record"](records)
        return UtilityExecution(
            records, failure == "interrupted", failure == "interrupted"
        )

    monkeypatch.setattr(runner, "execute_utility_queries", execute)

    def revoke(owned: Any, corpus: Any, query: Any, **kwargs: Any) -> Any:
        if failure == "revocation" and query.id == "org-2:change-notice":
            raise OSError("ExceptionSecretMarker")
        return next(
            event
            for event in cost_inputs()["revocations"]
            if event.before.query_id == query.id
        )

    monkeypatch.setattr(revocations, "capture_revocation", revoke)
    exit_code = cli.main(
        [
            "--embedding-root",
            str(tmp_path),
            "--provider",
            "fixture",
            "--qdrant-container",
            "ragelit-qdrant-test",
        ]
    )
    output = capsys.readouterr()
    assert output.err == ""
    assert "ExceptionSecretMarker" not in output.out
    result = json.loads(output.out)
    assert exit_code == (0 if failure == "none" else 2)
    assert result["query_count"] == (
        0 if failure == "index" else 1 if failure == "interrupted" else 87
    )
    final_path = config.report_dir / "utility-costs" / (result["run_id"] + ".json")
    final = validate_cost_report(final_path)
    assert not final.provisional
    assert final.summary.index_expected == 87
    assert final.summary.index_recorded == (
        1 if failure == "index" else 0 if failure == "reused" else 87
    )
    assert final.summary.index_build_ms == (
        None if failure in {"index", "reused"} else 174.0
    )
    assert final.summary.observed_ingestion_ms == (
        2.0 if failure == "index" else None if failure == "reused" else 174.0
    )
    assert final.summary.revocation_expected == 3
    assert final.summary.revocation_measured == (
        0
        if failure in {"index", "interrupted"}
        else 1
        if failure == "revocation"
        else 3
    )
    assert final.summary.revocation_mean_ms == (
        None if failure in {"index", "interrupted", "revocation"} else 20.0
    )
    assert final.summary.database_bytes == (
        None if failure in {"index", "storage"} else 32768
    )
    assert final.exit_code == exit_code
    assert final.runtime_failed == (
        failure in {"index", "storage", "revocation", "interrupted"}
    )
    assert final.coverage_complete == (failure == "none")
    snapshots = [
        path
        for path in (config.report_dir / "cost-checkpoints").rglob("*.json")
        if ".sha256." not in path.name
    ]
    assert snapshots
    for path in snapshots:
        snapshot = validate_cost_report(path)
        assert snapshot.provisional
        assert snapshot.exit_code == 2
    if failure == "index":
        assert final.utility is None
        assert final.index is not None and final.index.runtime_failed
    else:
        utility = validate_utility_report(
            config.report_dir / (result["run_id"] + ".json")
        )
        assert final.utility == utility
        if failure in {"storage", "revocation", "reused"}:
            assert utility.exit_code == 0
            assert final.exit_code == 2
