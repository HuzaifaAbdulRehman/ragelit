from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import logging
import os
import subprocess
import sys
import warnings
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from app.audits.contracts import AuditCaseResult
    from app.audits.target import BundledAuditTarget, PreparedPack
    from app.audits.workspace import AuditConfiguration, AuditWorkspace


class _ArgumentFailure(Exception):
    pass


class _ParserExit(Exception):
    def __init__(self, status: int) -> None:
        self.status = status


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _ArgumentFailure

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        raise _ParserExit(status)


@contextmanager
def _quiet_dependencies() -> Iterator[None]:
    sys.stdout.flush()
    sys.stderr.flush()
    native_runtime = ctypes.CDLL("ucrtbase" if os.name == "nt" else None)
    native_runtime.fflush.argtypes = [ctypes.c_void_p]
    if native_runtime.fflush(None) != 0:
        raise OSError("audit_stream_flush_failed")
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with (
            open(os.devnull, "w", encoding="utf-8") as destination,
            redirect_stdout(destination),
            redirect_stderr(destination),
            warnings.catch_warnings(),
            ExitStack() as native_streams,
        ):
            for descriptor in (1, 2):
                original = os.dup(descriptor)
                native_streams.callback(os.close, original)
                native_streams.callback(os.dup2, original, descriptor)
                os.dup2(destination.fileno(), descriptor)
            warnings.simplefilter("ignore")
            try:
                yield
            finally:
                destination.flush()
                if native_runtime.fflush(None) != 0:
                    raise OSError("audit_stream_flush_failed")
    finally:
        logging.disable(previous)


def _configuration() -> AuditConfiguration:
    from app.audits.workspace import AuditConfiguration

    names = {
        "environment": "ENVIRONMENT",
        "database_admin_url": "DATABASE_ADMIN_URL",
        "qdrant_url": "QDRANT_URL",
        "root": "ROOT",
        "application_password": "APPLICATION_PASSWORD",
        "fixture_password": "FIXTURE_PASSWORD",
    }
    values: dict[str, object] = {
        field: os.environ[f"RAGELIT_AUDIT_{suffix}"] for field, suffix in names.items()
    }
    if timeout := os.environ.get("RAGELIT_AUDIT_QDRANT_TIMEOUT_SECONDS"):
        values["qdrant_timeout_seconds"] = int(timeout)
    return AuditConfiguration.model_validate(values)


def _repository_metadata() -> dict[str, object]:
    root = Path(__file__).resolve().parents[3]

    def git(*arguments: str) -> str:
        return subprocess.run(
            ["git", "--no-optional-locks", "-C", str(root), *arguments],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()

    return {
        "git_revision": git("rev-parse", "HEAD"),
        "git_dirty": bool(git("status", "--porcelain", "--untracked-files=normal")),
        "lock_hashes": tuple(
            hashlib.sha256((root / filename).read_bytes()).hexdigest()
            for filename in ("backend/uv.lock", "frontend/package-lock.json")
        ),
    }


def execute_cases(
    target: BundledAuditTarget,
    pack: PreparedPack,
    selected: set[str] | None,
) -> tuple[tuple[AuditCaseResult, ...], bool]:
    from app.audits.contracts import AuditObservation
    from app.audits.scoring import score_case

    results: list[AuditCaseResult] = []
    failed = False
    for control in pack.cases:
        if selected is not None and control.id not in selected:
            continue
        try:
            observed = target.execute(control)
        except (Exception, KeyboardInterrupt):
            last_observation = target.last_observation
            if (
                isinstance(last_observation, AuditObservation)
                and last_observation.case_id == control.id
            ):
                results.append(score_case(control, last_observation))
            failed = True
            break
        results.append(score_case(control, observed))
    return tuple(results), failed


def finish_partial_run(
    workspace: AuditWorkspace,
    run_id: UUID,
    selected: set[str] | None,
    *,
    runtime_failed: bool,
) -> None:
    if selected is None or runtime_failed:
        return
    skipped = {
        key: instance.model_copy(update={"state": "skipped"})
        for key, instance in workspace.bindings.instances.items()
        if key.startswith(f"{run_id}:")
        and key.split(":", 1)[1] not in selected
        and instance.state == "incomplete"
    }
    if skipped:
        with workspace.mutation():
            workspace.bindings = workspace.bindings.model_copy(
                update={"instances": workspace.bindings.instances | skipped}
            )


def _diagnostic(code: str, exit_code: int) -> tuple[int, dict[str, Any]]:
    return exit_code, {"code": code, "exit_code": exit_code}


def _run(options: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    from app.audits.contracts import AuditCase, RunMetadata
    from app.audits.embeddings import FixtureEmbeddings
    from app.audits.fixtures import generate_fixtures
    from app.audits.reports import build_report, write_report
    from app.audits.seeding import FixtureCitingProvider
    from app.audits.target import BundledAuditTarget, prepare_pack
    from app.audits.workspace import AuditWorkspace, FixtureBindings

    template = generate_fixtures()
    selected = set(options.case) if options.case is not None else None
    if selected is not None and (
        len(selected) != len(options.case)
        or not selected.issubset({control.id for control in template.cases})
    ):
        return _diagnostic("audit_invalid_arguments", 2)
    try:
        config = _configuration()
    except Exception:
        return _diagnostic("audit_invalid_configuration", 2)
    try:
        repository = _repository_metadata()
        RunMetadata.model_validate(
            repository
            | {
                "profile": options.profile,
                "pack_id": template.pack_id,
                "generator_id": template.generator_id,
                "embedding_id": FixtureEmbeddings.identifier,
                "provider_id": FixtureCitingProvider.identifier,
                "template_hash": template.checksum,
                "binding_hash": "0" * 64,
                "config_hash": config.config_hash,
            }
        )
    except Exception:
        return _diagnostic("audit_metadata_failed", 2)
    run_id = uuid4()
    cases = tuple(
        AuditCase(id=control.id, expected_status=control.expected_status)
        for control in template.cases
    )
    results: tuple[AuditCaseResult, ...] = ()
    bindings: FixtureBindings | None = None
    opened = False
    runtime_failed = False
    try:
        with AuditWorkspace(config, template) as workspace:
            opened = True
            try:
                pack = prepare_pack(workspace, run_id)
                cases = pack.cases
                target = BundledAuditTarget(
                    workspace, pack, profile=options.profile, lab=options.lab
                )
                results, runtime_failed = execute_cases(target, pack, selected)
                finish_partial_run(
                    workspace, run_id, selected, runtime_failed=runtime_failed
                )
            except (Exception, KeyboardInterrupt):
                runtime_failed = True
            finally:
                bindings = FixtureBindings.model_validate_json(
                    config.bindings_path.read_bytes()
                )
    except (Exception, KeyboardInterrupt):
        if not opened:
            return _diagnostic("audit_workspace_failed", 2)
        runtime_failed = True
    if (
        bindings is None
        or bindings.workspace_id != config.workspace_id
        or bindings.template_hash != template.checksum
    ):
        return _diagnostic("audit_metadata_failed", 2)
    metadata = RunMetadata.model_validate(
        repository
        | {
            "profile": options.profile,
            "pack_id": template.pack_id,
            "generator_id": template.generator_id,
            "embedding_id": FixtureEmbeddings.identifier,
            "provider_id": FixtureCitingProvider.identifier,
            "template_hash": template.checksum,
            "binding_hash": bindings.checksum,
            "config_hash": config.config_hash,
        }
    )
    report = build_report(
        cases, results, metadata, runtime_failed=runtime_failed
    ).model_copy(update={"run_id": run_id})
    try:
        config.validate_paths()
        content = report.model_dump_json()
        if any(
            secret in content
            for secret in (
                config.application_password.get_secret_value(),
                config.fixture_password.get_secret_value(),
                "AUDITCANARY",
                "Bearer ",
            )
        ):
            raise ValueError("unsafe_audit_artifact")
        write_report(report, config.report_dir)
    except (Exception, KeyboardInterrupt):
        return _diagnostic("audit_report_write_failed", 2)
    return report.exit_code, {
        "code": "audit_runtime_failed"
        if runtime_failed
        else "audit_incomplete"
        if report.exit_code == 2
        else "audit_complete",
        "exit_code": report.exit_code,
        "run_id": str(run_id),
        "case_count": len(results),
    }


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(
        prog="ragelit-audit",
        description=(
            "Run synthetic access-control checks against an owned "
            "local audit workspace."
        ),
    )
    parser.add_argument(
        "--profile", choices=("safe", "vulnerable", "deny_all"), default="safe"
    )
    parser.add_argument(
        "--lab",
        action="store_true",
        help="Explicitly allow a deliberately broken synthetic target.",
    )
    parser.add_argument(
        "--case",
        action="append",
        help="Run one case; partial inventories always return 2.",
    )
    parser.add_argument(
        "--validate-reports",
        type=Path,
        help=(
            "Validate the safe/vulnerable/deny-all release artifacts "
            "without running cases."
        ),
    )
    try:
        options = parser.parse_args(argv)
    except _ParserExit as stopped:
        return stopped.status
    except _ArgumentFailure:
        code, output = _diagnostic("audit_invalid_arguments", 2)
    else:
        if options.validate_reports is not None:
            if options.profile != "safe" or options.lab or options.case is not None:
                code, output = _diagnostic("audit_invalid_arguments", 2)
            else:
                with _quiet_dependencies():
                    try:
                        from app.audits.artifacts import validate_release_directory

                        validate_release_directory(options.validate_reports)
                        code, output = _diagnostic("audit_artifacts_valid", 0)
                    except (Exception, KeyboardInterrupt):
                        code, output = _diagnostic(
                            "audit_artifact_validation_failed", 2
                        )
        elif options.profile != "safe" and not options.lab:
            code, output = _diagnostic("audit_lab_opt_in_required", 2)
        else:
            with _quiet_dependencies():
                try:
                    code, output = _run(options)
                except (Exception, KeyboardInterrupt):
                    code, output = _diagnostic("audit_runtime_failed", 2)
    print(json.dumps(output, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
