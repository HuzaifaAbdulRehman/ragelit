import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.audits.workspace import AuditWorkspaceError
from tests.unit.audits.test_artifacts import partial_artifact, rewrite_with_receipt


def configuration(root: Path, timeout: float = 10) -> Any:
    from app.audit_jobs.execution import AuditWorkerConfiguration

    return AuditWorkerConfiguration.model_validate(
        {
            "environment": "test",
            "database_admin_url": "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/postgres",
            "qdrant_url": "http://127.0.0.1:6333",
            "root": root,
            "application_password": "SyntheticApplicationPassword",
            "fixture_password": "SyntheticFixturePassword",
            "timeout_seconds": timeout,
        }
    )


def child(monkeypatch: pytest.MonkeyPatch, program: str) -> list[Any]:
    original = subprocess.Popen
    captured: list[Any] = []

    def launch(command: list[str], **kwargs: Any) -> Any:
        captured.append((command, kwargs))
        process = original([sys.executable, "-c", program], **kwargs)
        captured.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", launch)
    return captured


def artifact(root: Path, config: Any, request_id: UUID, profile: str = "safe") -> Path:
    from app.audit_jobs.execution import workspace_configuration

    owned = workspace_configuration(config, request_id)
    path = partial_artifact(owned.report_dir, profile)
    payload = json.loads(path.read_bytes())
    payload["metadata"]["config_hash"] = owned.config_hash
    rewrite_with_receipt(path, payload)
    return path


def diagnostic(path: Path, exit_code: int = 2) -> str:
    return json.dumps(
        {
            "code": "audit_incomplete",
            "exit_code": exit_code,
            "run_id": path.stem,
            "case_count": 0,
        }
    )


def test_cli_uses_fixed_safe_command_and_clean_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.audit_jobs.execution import run_cli

    config, request_id = configuration(tmp_path), uuid4()
    path = artifact(tmp_path, config, request_id)
    captured = child(monkeypatch, f"print({diagnostic(path)!r}); raise SystemExit(2)")
    monkeypatch.setenv("RAGELIT_SECRET_KEY", "SyntheticOrdinarySecret")
    monkeypatch.setenv("GROQ_API_KEY", "SyntheticProviderSecret")
    monkeypatch.setenv("PGHOSTADDR", "192.0.2.1")
    outcome = run_cli(config, request_id, heartbeat=lambda: True)
    command, kwargs = captured[0]
    assert command == [sys.executable, "-m", "app.audits.cli"]
    assert kwargs.get("shell", False) is False
    assert (
        not {"RAGELIT_SECRET_KEY", "GROQ_API_KEY", "PGHOSTADDR"} & kwargs["env"].keys()
    )
    assert outcome.exit_code == 2 and not outcome.recovery_required
    assert outcome.report_id == UUID(path.stem) != request_id
    assert outcome.report_content is not None
    assert outcome.report_content.encode("utf-8") == path.read_bytes()
    assert outcome.report_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert captured[1].poll() == 2


@pytest.mark.parametrize(
    "change",
    ["diagnostic", "stdout", "stderr", "hash", "uuid", "profile", "exit", "config"],
)
def test_invalid_artifact_cannot_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    from app.audit_jobs.execution import run_cli

    config, request_id = configuration(tmp_path), uuid4()
    path = artifact(
        tmp_path, config, request_id, "vulnerable" if change == "profile" else "safe"
    )
    payload = json.loads(diagnostic(path))
    if change == "hash":
        path.write_bytes(path.read_bytes() + b" ")
    if change == "uuid":
        payload["run_id"] = str(uuid4())
    if change == "exit":
        payload["exit_code"] = 0
    if change == "config":
        report = json.loads(path.read_bytes())
        report["metadata"]["config_hash"] = "0" * 64
        rewrite_with_receipt(path, report)
    output = "not-json" if change == "diagnostic" else json.dumps(payload)
    program = f"print({output!r}); raise SystemExit(2)"
    if change in {"stdout", "stderr"}:
        program = f"import os; os.write({1 if change == 'stdout' else 2}, b'x' * 70000)"
    captured = child(monkeypatch, program)
    outcome = run_cli(config, request_id, heartbeat=lambda: True)
    assert outcome.exit_code == 2
    assert outcome.report_content is None
    assert outcome.error_code is not None
    assert captured[1].poll() is not None


def test_timeout_reaps_child_before_return(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.audit_jobs.execution import run_cli

    captured = child(monkeypatch, "import time; time.sleep(60)")
    outcome = run_cli(configuration(tmp_path, 0.2), uuid4(), heartbeat=lambda: True)
    assert outcome.exit_code == 2 and outcome.error_code == "audit_timeout"
    assert captured[1].poll() is not None
    assert not outcome.recovery_required


def test_heartbeat_loss_reaps_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.audit_jobs import execution

    monkeypatch.setattr(execution, "HEARTBEAT_SECONDS", 0.05)
    calls = iter([True, False])
    captured = child(monkeypatch, "import time; time.sleep(60)")
    outcome = execution.run_cli(
        configuration(tmp_path), uuid4(), heartbeat=lambda: next(calls)
    )
    assert outcome.error_code == "audit_lease_lost"
    assert captured[1].poll() is not None


def test_unconfirmed_shutdown_requires_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.audit_jobs import execution

    captured = child(monkeypatch, "import time; time.sleep(60)")
    monkeypatch.setattr(execution, "stop_child", lambda process: False)
    try:
        outcome = execution.run_cli(
            configuration(tmp_path, 0.2), uuid4(), heartbeat=lambda: True
        )
        assert outcome.exit_code == 2 and outcome.recovery_required
        assert outcome.report_content is None
    finally:
        captured[1].kill()
        captured[1].wait(timeout=5)


def test_configuration_requires_explicit_audit_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.audit_jobs.execution import configuration_from_environment

    monkeypatch.delenv("RAGELIT_AUDIT_ENVIRONMENT", raising=False)
    monkeypatch.setenv("RAGELIT_DATABASE_ADMIN_URL", "SyntheticOrdinaryTarget")
    with pytest.raises(ValueError, match="audit_invalid_configuration"):
        configuration_from_environment()


@pytest.mark.parametrize(
    "field,value",
    [
        (
            "database_admin_url",
            "postgresql+psycopg://postgres:postgres@example.com/postgres",
        ),
        ("qdrant_url", "https://example.com"),
        ("root", Path("relative")),
        ("timeout_seconds", 1801),
        ("environment", "production"),
    ],
)
def test_configuration_keeps_owned_loopback_guards(
    tmp_path: Path, field: str, value: Any
) -> None:
    from app.audit_jobs.execution import AuditWorkerConfiguration

    values = configuration(tmp_path).model_dump()
    values[field] = value
    with pytest.raises((ValidationError, AuditWorkspaceError)):
        AuditWorkerConfiguration.model_validate(values)
