import subprocess
import sys

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
