import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from app.audits import isolation_cli
from app.audits import target as target_module
from app.audits import workspace as workspace_module
from app.audits.fixtures import FixtureTemplate
from app.audits.isolation_reports import write_isolation_report
from app.audits.target import PreparedPack
from app.audits.workspace import AuditConfiguration, FixtureBindings
from tests.unit.audits.test_isolation_reports import publish, report
from tests.unit.audits.test_workspace import configuration


def unreachable(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("ExceptionSecretMarker must not reach configuration")


@pytest.mark.parametrize(
    "arguments",
    [
        ["--strategy", "unknown"],
        ["--strategy", "lab_post_filter"],
        ["--unknown", "ExceptionSecretMarker"],
        ["--validate-report", "missing.json", "--strategy", "shared_pre_filter"],
        ["--validate-reports", "missing", "--lab"],
        ["--validate-report", "missing.json", "--validate-reports", "missing"],
    ],
)
def test_invalid_arguments_fail_before_any_workspace(
    arguments: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(isolation_cli, "_configuration", unreachable)
    assert isolation_cli.main(arguments) == 2
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {
        "code": "isolation_invalid_arguments",
        "exit_code": 2,
    }
    assert "ExceptionSecretMarker" not in output.out


def test_configuration_error_is_fixed_redacted_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(isolation_cli, "_configuration", unreachable)
    assert isolation_cli.main(["--strategy", "shared_pre_filter"]) == 2
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {
        "code": "isolation_invalid_configuration",
        "exit_code": 2,
    }
    assert "ExceptionSecretMarker" not in output.out


@pytest.mark.parametrize("directory", [False, True])
def test_validation_is_offline_and_does_not_open_a_workspace(
    tmp_path: Path,
    directory: bool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(isolation_cli, "_configuration", unreachable)
    if directory:
        publish(tmp_path)
        arguments = ["--validate-reports", str(tmp_path)]
    else:
        path = write_isolation_report(report(), tmp_path)
        arguments = ["--validate-report", str(path)]
    assert isolation_cli.main(arguments) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {
        "code": "isolation_artifacts_valid",
        "exit_code": 0,
    }


def test_invalid_artifact_diagnostic_does_not_echo_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        isolation_cli.main(
            ["--validate-report", str(tmp_path / "ExceptionSecretMarker.json")]
        )
        == 2
    )
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {
        "code": "isolation_artifact_validation_failed",
        "exit_code": 2,
    }
    assert "ExceptionSecretMarker" not in output.out


def test_failure_after_pack_preparation_retains_empty_inconclusive_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.audits.isolation_reports import validate_isolation_report

    config = configuration(tmp_path)
    prepared = report()

    @contextmanager
    def workspace(
        current: AuditConfiguration, template: FixtureTemplate
    ) -> Iterator[SimpleNamespace]:
        current.root.mkdir()
        bindings = FixtureBindings(
            workspace_id=current.workspace_id,
            template_hash=template.checksum,
            collection=current.name,
        )
        current.bindings_path.write_text(bindings.model_dump_json(), encoding="utf-8")
        yield SimpleNamespace(
            store=SimpleNamespace(collection_names=lambda: (current.name,))
        )

    def pack(owned: Any, run_id: UUID) -> PreparedPack:
        return PreparedPack(
            run_id,
            prepared.cases,
            MappingProxyType({}),
            MappingProxyType({}),
            frozenset(),
        )

    monkeypatch.setattr(isolation_cli, "_configuration", lambda: config)
    monkeypatch.setattr(
        isolation_cli,
        "_repository_metadata",
        lambda: {
            "git_revision": "a" * 40,
            "git_dirty": False,
            "lock_hashes": ("b" * 64,),
        },
    )
    monkeypatch.setattr(workspace_module, "AuditWorkspace", workspace)
    monkeypatch.setattr(target_module, "prepare_pack", pack)
    monkeypatch.setattr(target_module, "BundledAuditTarget", unreachable)
    assert isolation_cli.main(["--strategy", "shared_pre_filter"]) == 2
    output = capsys.readouterr()
    assert output.err == "" and "ExceptionSecretMarker" not in output.out
    status = json.loads(output.out)
    assert status["code"] == "isolation_runtime_failed"
    assert status["case_count"] == 0
    checked = validate_isolation_report(config.report_dir / f"{status['run_id']}.json")
    assert checked.audit.runtime_failed and checked.audit.exit_code == 2
    assert checked.audit.results == () and len(checked.cases) == 51
