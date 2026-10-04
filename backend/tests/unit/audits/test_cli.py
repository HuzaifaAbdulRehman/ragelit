import json
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

from app.audits.contracts import AuditCase, AuditObservation
from app.audits.fixtures import generate_fixtures
from app.audits.reports import build_report
from app.audits.target import BundledAuditTarget, PreparedPack
from app.audits.workspace import AuditConfiguration, AuditWorkspace, InstanceBinding
from tests.unit.audits.support import FORBIDDEN, case, metadata, observation


def invoke_cli(
    arguments: list[str], values: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("RAGELIT_AUDIT_")
    }
    environment.update(values or {})
    return subprocess.run(
        [sys.executable, "-m", "app.audits.cli", *arguments],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60 if os.name == "nt" else 30,
        check=False,
    )


@pytest.mark.parametrize(
    "arguments",
    [
        ["--unknown", "SyntheticSensitiveInputNeverEcho"],
        ["--profile", "SyntheticSensitiveInputNeverEcho"],
        ["--case", "SyntheticSensitiveInputNeverEcho"],
        ["--case", "org-1:organization", "--case", "org-1:organization"],
    ],
)
def test_invalid_arguments_return_two_without_echoing_input(
    arguments: list[str],
) -> None:
    process = invoke_cli(arguments)
    assert process.returncode == 2
    assert json.loads(process.stdout) == {
        "code": "audit_invalid_arguments",
        "exit_code": 2,
    }
    assert process.stderr == ""
    assert "SyntheticSensitiveInputNeverEcho" not in process.stdout


@pytest.mark.parametrize("profile", ["vulnerable", "deny_all"])
def test_broken_profile_requires_opt_in_before_configuration(profile: str) -> None:
    process = invoke_cli(["--profile", profile])
    assert process.returncode == 2
    assert json.loads(process.stdout) == {
        "code": "audit_lab_opt_in_required",
        "exit_code": 2,
    }
    assert process.stderr == ""


def test_configuration_is_explicit_and_ordinary_app_environment_is_ignored() -> None:
    process = invoke_cli(
        [], {"RAGELIT_DATABASE_ADMIN_URL": "SyntheticSensitiveInputNeverEcho"}
    )
    assert process.returncode == 2
    assert json.loads(process.stdout) == {
        "code": "audit_invalid_configuration",
        "exit_code": 2,
    }
    assert process.stderr == ""


def test_help_does_not_need_services_or_configuration() -> None:
    process = invoke_cli(["--help"])
    assert process.returncode == 0
    assert "--lab" in process.stdout
    assert "--case" in process.stdout
    assert process.stderr == ""


@pytest.mark.parametrize("interrupted", [False, True])
def test_dependency_silencing_covers_native_output_and_restores_streams(
    interrupted: bool,
) -> None:
    program = """
import ctypes
import os
from app.audits.cli import _quiet_dependencies

runtime = ctypes.CDLL('ucrtbase' if os.name == 'nt' else None)
runtime.fflush.argtypes = [ctypes.c_void_p]
runtime.setvbuf.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t,
]
runtime.fwrite.argtypes = [
    ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_void_p,
]
runtime.fwrite.restype = ctypes.c_size_t
if os.name == 'nt':
    runtime.__acrt_iob_func.argtypes = [ctypes.c_uint]
    runtime.__acrt_iob_func.restype = ctypes.c_void_p
    native_stdout = runtime.__acrt_iob_func(1)
else:
    native_stdout = ctypes.c_void_p.in_dll(runtime, 'stdout')
buffer = ctypes.create_string_buffer(4096)
assert runtime.setvbuf(native_stdout, buffer, 0, len(buffer)) == 0
message = b'SyntheticBufferedNativeMarker\\n'
try:
    with _quiet_dependencies():
        os.write(1, b'SyntheticNativeStdoutMarker\\n')
        os.write(2, b'SyntheticNativeStderrMarker\\n')
        print('SyntheticPythonMarker', flush=True)
        assert runtime.fwrite(message, 1, len(message), native_stdout) == len(message)
        if INTERRUPTED:
            raise RuntimeError('SyntheticInterruptionMarker')
except RuntimeError:
    pass
assert runtime.fflush(None) == 0
os.write(1, b'visible stdout\\n')
os.write(2, b'visible stderr\\n')
""".replace("INTERRUPTED", repr(interrupted))
    process = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert process.returncode == 0
    assert process.stdout == "visible stdout\n"
    assert process.stderr == "visible stderr\n"


def test_artifact_validation_is_read_only_and_has_bounded_diagnostics(
    tmp_path: Path,
) -> None:
    process = invoke_cli(
        ["--validate-reports", str(tmp_path / "SyntheticSensitiveInputNeverEcho")]
    )
    assert process.returncode == 2
    assert json.loads(process.stdout) == {
        "code": "audit_artifact_validation_failed",
        "exit_code": 2,
    }
    assert process.stderr == ""
    assert "SyntheticSensitiveInputNeverEcho" not in process.stdout
    assert list(tmp_path.iterdir()) == []


def test_invalid_configuration_does_not_print_urls_or_passwords(tmp_path: Path) -> None:
    process = invoke_cli(
        [],
        {
            "RAGELIT_AUDIT_ENVIRONMENT": "production",
            "RAGELIT_AUDIT_DATABASE_ADMIN_URL": "postgresql+psycopg://example:SyntheticSensitiveInputNeverEcho@127.0.0.1:9/ordinary",
            "RAGELIT_AUDIT_QDRANT_URL": "http://127.0.0.1:6333",
            "RAGELIT_AUDIT_ROOT": str(tmp_path),
            "RAGELIT_AUDIT_APPLICATION_PASSWORD": "SyntheticApplicationPasswordMarker",
            "RAGELIT_AUDIT_FIXTURE_PASSWORD": "SyntheticFixturePasswordMarker",
        },
    )
    assert process.returncode == 2
    assert json.loads(process.stdout) == {
        "code": "audit_invalid_configuration",
        "exit_code": 2,
    }
    assert process.stderr == ""
    assert "SyntheticSensitiveInputNeverEcho" not in process.stdout
    assert "postgresql" not in process.stdout


@pytest.mark.parametrize("exposed,expected_status", [(False, "pass"), (True, "fail")])
def test_runtime_failure_keeps_last_evidence_and_cannot_pass(
    exposed: bool, expected_status: str
) -> None:
    from app.audits.cli import execute_cases

    observed = observation()
    if exposed:
        first = observed.boundaries[0].model_copy(update={"chunk_ids": (FORBIDDEN,)})
        observed = observed.model_copy(
            update={"boundaries": (first, *observed.boundaries[1:])}
        )

    class InterruptedTarget:
        last_observation: AuditObservation | None = None

        def execute(self, control: AuditCase) -> AuditObservation:
            assert control.id == "ORG-001"
            self.last_observation = observed
            raise RuntimeError("SyntheticSensitiveRuntimeMarker")

    pack = PreparedPack(uuid4(), (case(),), {}, {}, frozenset())
    results, failed = execute_cases(
        cast(BundledAuditTarget, InterruptedTarget()), pack, None
    )
    report = build_report(pack.cases, results, metadata(), runtime_failed=failed)
    assert failed
    assert report.exit_code == 2
    assert not report.coverage_complete
    assert report.results[0].status == expected_status
    assert "SyntheticSensitiveRuntimeMarker" not in report.model_dump_json()


@pytest.mark.parametrize(
    "selected,failed,expected",
    [
        ({"org-1:grant-revoked"}, False, "skipped"),
        ({"org-1:grant-revoked"}, True, "incomplete"),
        (None, False, "incomplete"),
    ],
)
def test_partial_completion_marks_only_intentionally_skipped_current_instances(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selected: set[str] | None,
    failed: bool,
    expected: str,
) -> None:
    from app.audits.cli import finish_partial_run

    config = AuditConfiguration.model_validate(
        {
            "environment": "test",
            "database_admin_url": "postgresql+psycopg://postgres:postgres@127.0.0.1/ragelit_audit_partial",
            "qdrant_url": "http://127.0.0.1:6333",
            "root": tmp_path / "ragelit_audit_partial",
            "application_password": "SyntheticApplicationPasswordMarker",
            "fixture_password": "SyntheticFixturePasswordMarker",
        }
    )
    workspace = AuditWorkspace(config, generate_fixtures())
    run_id = uuid4()
    executed = f"{run_id}:org-1:grant-revoked"
    skipped = f"{run_id}:org-2:grant-revoked"
    interrupted = f"{uuid4()}:org-3:grant-revoked"
    workspace.bindings = workspace.bindings.model_copy(
        update={
            "instances": {
                executed: InstanceBinding(state="complete"),
                skipped: InstanceBinding(),
                interrupted: InstanceBinding(),
            }
        }
    )

    @contextmanager
    def local_manifest_mutation() -> Iterator[None]:
        yield

    monkeypatch.setattr(workspace, "mutation", local_manifest_mutation)
    finish_partial_run(workspace, run_id, selected, runtime_failed=failed)
    assert workspace.bindings.instances[executed].state == "complete"
    assert workspace.bindings.instances[skipped].state == expected
    assert workspace.bindings.instances[interrupted].state == "incomplete"
    assert not config.root.exists()
