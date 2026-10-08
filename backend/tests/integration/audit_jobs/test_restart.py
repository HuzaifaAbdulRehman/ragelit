import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.audit_jobs.models import AuditRun
from tests.api.tenant_support import TenantApiSeed
from tests.integration.audit_jobs.test_claims import queued

_WORKER = """
import sys, time
from pathlib import Path
from app.audits.cli import _quiet_dependencies
with _quiet_dependencies():
    from app.workers import audit
    from app.audit_jobs.contracts import CliOutcome

def execute(config, request_id, *, heartbeat):
    Path(sys.argv[4]).write_text(str(request_id))
    if sys.argv[3] == "pause":
        time.sleep(60)
    return CliOutcome(2, error_code="synthetic_restart_probe")

audit.run_cli = execute
def configuration():
    return audit.AuditWorkerConfiguration.model_validate({
    "environment": "test",
    "database_admin_url": "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/postgres",
    "qdrant_url": "http://127.0.0.1:6333",
    "root": Path(sys.argv[2]),
    "application_password": "SyntheticApplicationPassword",
    "fixture_password": "SyntheticFixturePassword",
    "timeout_seconds": 10,
    })

audit.configuration_from_environment = configuration
raise SystemExit(audit.main(["--organization-id", sys.argv[1], "--once"]))
"""


def test_fresh_worker_requires_recovery_after_process_death(
    tenant_database_engines: tuple[Engine, Engine],
    tenant_seed: TenantApiSeed,
    tmp_path: Path,
) -> None:
    admin, runtime = tenant_database_engines
    run_id = queued(tenant_database_engines, tenant_seed)
    organization = str(tenant_seed.organization_a_id)
    ready = tmp_path / "claimed-run"
    environment = dict(
        os.environ,
        RAGELIT_DATABASE_URL=runtime.url.render_as_string(hide_password=False),
    )
    command = [
        sys.executable,
        "-c",
        _WORKER,
        organization,
        str(tmp_path),
        "pause",
        str(ready),
    ]
    worker = subprocess.Popen(
        command, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        deadline = time.monotonic() + 60
        while worker.poll() is None:
            if ready.exists() and ready.read_text() == str(run_id):
                break
            assert time.monotonic() < deadline, "Owned worker did not claim its run"
            time.sleep(0.05)
        assert ready.exists(), "Owned worker exited before reaching audit execution"
        assert ready.read_text() == str(run_id)
        with Session(admin) as session:
            run = session.get(AuditRun, run_id)
            assert run is not None and run.state == "running"

        recovery = [
            sys.executable,
            "-m",
            "app.workers.audit",
            "--organization-id",
            organization,
            "--recover-run",
            str(run_id),
        ]
        unconfirmed = subprocess.run(
            recovery, env=environment, capture_output=True, text=True, timeout=60
        )
        assert unconfirmed.returncode == 2
        assert json.loads(unconfirmed.stdout)["code"] == "audit_invalid_arguments"

        worker.kill()
        worker.communicate(timeout=10)
        assert worker.poll() is not None
        with Session(admin) as session:
            run = session.get(AuditRun, run_id)
            assert run is not None and run.state == "running"
            assert run.outcome == "unknown" and run.report_content is None
            run.lease_until = datetime.now(UTC) - timedelta(seconds=1)
            session.commit()

        ready.unlink()
        command[-2] = "finish"
        restarted = subprocess.run(
            command, env=environment, capture_output=True, text=True, timeout=60
        )
        assert restarted.returncode == 0 and not ready.exists()
        with Session(admin) as session:
            run = session.get(AuditRun, run_id)
            assert run is not None and run.state == "recovery_required"
            assert run.outcome == "inconclusive" and run.exit_code == 2
            assert run.report_content is None

        recovered = subprocess.run(
            [*recovery, "--confirm-worker-stopped"],
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert recovered.returncode == 2
        assert json.loads(recovered.stdout) == {
            "code": "audit_recovery_complete",
            "exit_code": 2,
        }
        with Session(admin) as session:
            run = session.get(AuditRun, run_id)
            assert run is not None and run.state == "finished"
            assert run.outcome == "inconclusive" and run.exit_code == 2
            assert run.claim_id is None and run.lease_until is None
            assert run.report_content is None

        replacement = queued(tenant_database_engines, tenant_seed)
        resumed = subprocess.run(
            command, env=environment, capture_output=True, text=True, timeout=60
        )
        assert resumed.returncode == 0
        assert ready.read_text() == str(replacement)
        with Session(admin) as session:
            old, new = session.get(AuditRun, run_id), session.get(AuditRun, replacement)
            assert old is not None and old.outcome == "inconclusive"
            assert new is not None and new.state == "finished"
            assert new.error_code == "synthetic_restart_probe"
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.communicate(timeout=10)
