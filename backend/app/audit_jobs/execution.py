import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from sqlalchemy.engine import make_url

from app.audit_jobs.contracts import CliOutcome
from app.audits.artifacts import validate_report
from app.audits.reports import AuditReport
from app.audits.workspace import AuditConfiguration

HEARTBEAT_SECONDS = 15.0
OUTPUT_LIMIT = 64 * 1024
_ENVIRONMENT_KEYS = {
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PATHEXT",
}
_ERROR_CODES = {
    "audit_invalid_configuration",
    "audit_metadata_failed",
    "audit_workspace_failed",
    "audit_report_write_failed",
    "audit_runtime_failed",
    "audit_incomplete",
}


class AuditWorkerConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    environment: Literal["local", "test"]
    database_admin_url: SecretStr = Field(repr=False)
    qdrant_url: str
    root: Path
    application_password: SecretStr = Field(repr=False, min_length=16)
    fixture_password: SecretStr = Field(repr=False, min_length=16)
    timeout_seconds: float = Field(default=1800, gt=0, le=1800)
    qdrant_timeout_seconds: int = Field(default=30, ge=1, le=30)

    @model_validator(mode="after")
    def guard_workspace(self) -> Self:
        workspace_configuration(self, UUID(int=0))
        return self


def workspace_configuration(
    configuration: AuditWorkerConfiguration,
    request_id: UUID,
) -> AuditConfiguration:
    name = f"ragelit_audit_{request_id.hex}"
    url = make_url(configuration.database_admin_url.get_secret_value()).set(
        database=name
    )
    return AuditConfiguration(
        environment=configuration.environment,
        database_admin_url=SecretStr(url.render_as_string(hide_password=False)),
        qdrant_url=configuration.qdrant_url,
        root=configuration.root / name,
        application_password=configuration.application_password,
        fixture_password=configuration.fixture_password,
        qdrant_timeout_seconds=configuration.qdrant_timeout_seconds,
    )


def configuration_from_environment() -> AuditWorkerConfiguration:
    fields = (
        "environment",
        "database_admin_url",
        "qdrant_url",
        "root",
        "application_password",
        "fixture_password",
    )
    try:
        values = {
            field: os.environ[f"RAGELIT_AUDIT_{field.upper()}"] for field in fields
        }
        for field in ("timeout_seconds", "qdrant_timeout_seconds"):
            if value := os.environ.get(f"RAGELIT_AUDIT_{field.upper()}"):
                values[field] = value
        return AuditWorkerConfiguration.model_validate(values)
    except Exception as error:
        raise ValueError("audit_invalid_configuration") from error


def _child_environment(owned: AuditConfiguration) -> dict[str, str]:
    database_url = owned.database_admin_url.get_secret_value()
    application_password = owned.application_password.get_secret_value()
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in _ENVIRONMENT_KEYS
    }
    environment["PYTHONUTF8"] = "1"
    environment.update(
        {
            "RAGELIT_AUDIT_ENVIRONMENT": owned.environment,
            "RAGELIT_AUDIT_DATABASE_ADMIN_URL": database_url,
            "RAGELIT_AUDIT_QDRANT_URL": owned.qdrant_url,
            "RAGELIT_AUDIT_ROOT": str(owned.root),
            "RAGELIT_AUDIT_APPLICATION_PASSWORD": application_password,
            "RAGELIT_AUDIT_FIXTURE_PASSWORD": owned.fixture_password.get_secret_value(),
            "RAGELIT_AUDIT_QDRANT_TIMEOUT_SECONDS": str(owned.qdrant_timeout_seconds),
        }
    )
    return environment


def stop_child(process: subprocess.Popen[bytes]) -> bool:
    try:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        return process.poll() is not None
    except OSError:
        return process.poll() is not None
    except subprocess.TimeoutExpired:
        return False


def _read_output(stream: BinaryIO, buffer: bytearray, invalid: threading.Event) -> None:
    try:
        while chunk := stream.read(4096):
            available = OUTPUT_LIMIT - len(buffer)
            buffer.extend(chunk[:available])
            if len(chunk) > available:
                invalid.set()
    except OSError:
        invalid.set()
    finally:
        stream.close()


class _Diagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    code: str
    exit_code: Literal[0, 1, 2]
    run_id: str | None = None
    case_count: int | None = Field(default=None, ge=0, le=51)


def _validated_outcome(
    owned: AuditConfiguration,
    stdout: bytes,
    stderr: bytes,
    returncode: int,
) -> CliOutcome:
    if stderr:
        raise ValueError("audit_output_invalid")
    diagnostic = _Diagnostic.model_validate_json(stdout)
    if diagnostic.exit_code != returncode:
        raise ValueError("audit_output_invalid")
    if diagnostic.run_id is None:
        if returncode != 2 or diagnostic.code not in _ERROR_CODES:
            raise ValueError("audit_output_invalid")
        return CliOutcome(2, error_code=diagnostic.code)
    report_id = UUID(diagnostic.run_id)
    if diagnostic.run_id != str(report_id):
        raise ValueError("audit_output_invalid")
    owned.validate_paths()
    path = owned.report_dir / f"{report_id}.json"
    report = validate_report(path)
    with path.open("rb") as stream:
        content = stream.read(8 * 1024 * 1024 + 1)
    # Recheck the exact saved bytes after validation, not a reserialized model.
    if (
        len(content) > 8 * 1024 * 1024
        or AuditReport.model_validate_json(content) != report
    ):
        raise ValueError("audit_artifact_invalid")
    if (
        report.metadata.profile != "safe"
        or report.exit_code != returncode
        or report.metadata.config_hash != owned.config_hash
        or diagnostic.case_count != len(report.results)
        or diagnostic.code
        != (
            "audit_runtime_failed"
            if report.runtime_failed
            else "audit_incomplete"
            if returncode == 2
            else "audit_complete"
        )
    ):
        raise ValueError("audit_artifact_invalid")
    digest = hashlib.sha256(content).hexdigest()
    # The receipt must still match the bytes read for persistence.
    with path.with_suffix(".sha256.json").open("rb") as stream:
        receipt_bytes = stream.read(4097)
    if len(receipt_bytes) > 4096:
        raise ValueError("audit_artifact_invalid")
    receipt = json.loads(receipt_bytes)
    if receipt != {"filename": path.name, "sha256": digest}:
        raise ValueError("audit_artifact_invalid")
    if any(
        secret.encode() in content
        for secret in (
            owned.application_password.get_secret_value(),
            owned.fixture_password.get_secret_value(),
        )
    ):
        raise ValueError("audit_artifact_invalid")
    return CliOutcome(
        diagnostic.exit_code,
        content.decode("utf-8"),
        report_id,
        digest,
        diagnostic.code if returncode == 2 else None,
    )


def run_cli(
    configuration: AuditWorkerConfiguration,
    request_id: UUID,
    *,
    heartbeat: Callable[[], bool],
) -> CliOutcome:
    owned = workspace_configuration(configuration, request_id)
    process: subprocess.Popen[bytes] | None = None
    readers: list[threading.Thread] = []
    buffers = (bytearray(), bytearray())
    invalid = threading.Event()
    error_code: str | None = None
    stopped = True
    try:
        if not heartbeat():
            return CliOutcome(2, error_code="audit_lease_lost")
        process = subprocess.Popen(
            [sys.executable, "-m", "app.audits.cli"],
            shell=False,
            cwd=Path(__file__).resolve().parents[2],
            env=_child_environment(owned),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert process.stdout is not None and process.stderr is not None
        for stream, buffer in zip(
            (process.stdout, process.stderr), buffers, strict=True
        ):
            thread = threading.Thread(
                target=_read_output, args=(stream, buffer, invalid), daemon=True
            )
            thread.start()
            readers.append(thread)
        deadline = time.monotonic() + configuration.timeout_seconds
        renewal = time.monotonic() + HEARTBEAT_SECONDS
        while process.poll() is None:
            now = time.monotonic()
            if invalid.is_set():
                error_code = "audit_output_invalid"
                break
            if now >= deadline:
                error_code = "audit_timeout"
                break
            if now >= renewal:
                if not heartbeat():
                    error_code = "audit_lease_lost"
                    break
                renewal = now + HEARTBEAT_SECONDS
            time.sleep(0.05)
    except KeyboardInterrupt:
        error_code = "audit_interrupted"
    except Exception:
        error_code = "audit_execution_failed"
    finally:
        if process is not None:
            stopped = stop_child(process)
        if stopped:
            for thread in readers:
                thread.join(timeout=5)
            stopped = not any(thread.is_alive() for thread in readers)
    if not stopped:
        return CliOutcome(
            2, error_code="audit_shutdown_unconfirmed", recovery_required=True
        )
    if error_code:
        return CliOutcome(2, error_code=error_code)
    if invalid.is_set() or process is None or process.returncode is None:
        return CliOutcome(2, error_code="audit_output_invalid")
    try:
        return _validated_outcome(
            owned, bytes(buffers[0]), bytes(buffers[1]), process.returncode
        )
    except Exception:
        return CliOutcome(2, error_code="audit_artifact_invalid")
