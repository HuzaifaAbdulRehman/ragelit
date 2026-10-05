import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "arguments",
    [
        ["--recover-run", "00000000-0000-0000-0000-000000000000"],
        ["--confirm-worker-stopped"],
        [
            "--once",
            "--recover-run",
            "00000000-0000-0000-0000-000000000000",
            "--confirm-worker-stopped",
        ],
    ],
)
def test_recovery_arguments_require_explicit_exclusive_confirmation(
    arguments: list[str],
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.workers.audit",
            "--organization-id",
            "11111111-1111-4111-8111-111111111111",
            *arguments,
        ],
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 2
    assert b"audit_invalid_arguments" in result.stdout
    assert b"Traceback" not in result.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX worker termination")
@pytest.mark.parametrize("startup_noise", [False, True])
def test_sigterm_reaps_owned_child_before_worker_exit(
    tmp_path: Path, startup_noise: bool
) -> None:
    ready = tmp_path / "child-pid"
    outcome_path = tmp_path / "outcome.json"
    program = f"""
import json, os, subprocess, sys
from pathlib import Path
from uuid import uuid4
from app.audits.cli import _quiet_dependencies
with _quiet_dependencies():
    if {startup_noise!r}:
        os.write(2, b"SyntheticNativeStartupWarning\\n")
    from app.workers import audit
    from app.audit_jobs.execution import run_cli
    from tests.unit.audit_jobs.test_execution import configuration

original = subprocess.Popen
children = []
def launch(command, **kwargs):
    child_program = (
        "import os,time; from pathlib import Path; time.sleep(.2); "
        "Path({str(ready)!r}).write_text(str(os.getpid())); time.sleep(60)"
    )
    child = original([sys.executable, "-c", child_program], **kwargs)
    children.append(child)
    return child
subprocess.Popen = launch
class Engine:
    def dispose(self):
        assert not children or children[0].poll() is not None
audit.build_engine = lambda url: Engine()
audit.build_session_factory = lambda engine: None
audit.configuration_from_environment = lambda: configuration(
    Path({str(tmp_path)!r}), 60
)
def run_once(org_id, *, session_factory, configuration):
    outcome = run_cli(configuration, uuid4(), heartbeat=lambda: True)
    result = {{"code": outcome.error_code,
              "reaped": children[0].poll() is not None}}
    Path({str(outcome_path)!r}).write_text(json.dumps(result))
    if outcome.error_code == "audit_interrupted":
        raise KeyboardInterrupt
    return True
audit.run_once = run_once
raise SystemExit(audit.main(["--organization-id", str(uuid4()), "--once"]))
"""
    environment = dict(os.environ, RAGELIT_DATABASE_URL="synthetic-job-database")
    worker = subprocess.Popen(
        [sys.executable, "-c", program],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    owned_pid: int | None = None
    try:
        deadline = time.monotonic() + 60
        while not ready.exists() and worker.poll() is None:
            assert time.monotonic() < deadline, "Owned child did not start"
            time.sleep(0.05)
        assert ready.exists(), "Worker exited before starting its child"
        owned_pid = int(ready.read_text())
        worker.send_signal(signal.SIGTERM)
        stdout, stderr = worker.communicate(timeout=20)
        assert worker.returncode == 2
        assert json.loads(stdout) == {"code": "audit_interrupted", "exit_code": 2}
        assert not stderr
        assert json.loads(outcome_path.read_text()) == {
            "code": "audit_interrupted",
            "reaped": True,
        }
        with pytest.raises(ProcessLookupError):
            os.kill(owned_pid, 0)
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.wait(timeout=10)
        if owned_pid is not None and sys.platform != "win32":
            try:
                os.kill(owned_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
