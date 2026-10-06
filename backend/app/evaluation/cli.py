from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from app.audits.cli import (
    _ArgumentFailure,
    _configuration,
    _diagnostic,
    _Parser,
    _ParserExit,
    _quiet_dependencies,
    _repository_metadata,
)

if TYPE_CHECKING:
    from app.audits.workspace import AuditWorkspace
    from app.evaluation.reports import (
        BenchmarkProvenance,
        GenerationRecord,
        MachineRecord,
    )


def machine_record() -> MachineRecord:
    from app.evaluation.reports import MachineRecord

    system = platform.system()
    if system == "Windows":

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_phys", ctypes.c_ulonglong),
                ("avail_phys", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("avail_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("avail_virtual", ctypes.c_ulonglong),
                ("avail_extended_virtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        read = ctypes.windll.kernel32.GlobalMemoryStatusEx
        read.argtypes = [ctypes.POINTER(MemoryStatus)]
        read.restype = ctypes.c_int
        if not read(ctypes.byref(status)):
            raise OSError("utility_machine_metadata_failed")
        memory = int(status.total_phys)
    elif system == "Linux":
        sysconf = cast(Callable[[str], int], vars(os)["sysconf"])
        memory = sysconf("SC_PHYS_PAGES") * sysconf("SC_PAGE_SIZE")
    elif system == "Darwin":
        memory = int(
            subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            ).stdout.strip()
        )
    else:
        raise ValueError("utility_machine_metadata_failed")
    return MachineRecord.model_validate(
        {
            "os": system,
            "architecture": platform.machine(),
            "python_version": platform.python_version(),
            "logical_cpus": os.cpu_count(),
            "memory_bytes": memory,
        }
    )


def collect_provenance(
    workspace: AuditWorkspace, generation: GenerationRecord
) -> BenchmarkProvenance:
    from sqlalchemy import text

    from app.evaluation.dataset import generate_utility_corpus
    from app.evaluation.models import load_embedding_pins
    from app.evaluation.reports import BenchmarkProvenance

    workspace.validate_owned()
    with workspace.admin_engine.connect() as connection:
        postgres = str(connection.execute(text("SHOW server_version")).scalar_one())
    return BenchmarkProvenance.model_validate(
        _repository_metadata()
        | {
            "corpus_hash": generate_utility_corpus().checksum,
            "embedding_fingerprint": load_embedding_pins().fingerprint,
            "generation": generation,
            "machine": machine_record(),
            "postgres_version": postgres.split()[0],
            "qdrant_version": workspace.store.client.info().version,
        }
    )


def _generation(options: argparse.Namespace) -> GenerationRecord:
    from app.audits.injection_providers import LocalInjectionConfiguration
    from app.evaluation.reports import GenerationRecord

    local_flags = (
        options.base_url,
        options.model,
        options.weights_sha256,
        options.server_version,
    )
    if options.provider == "fixture":
        if any(value is not None for value in local_flags):
            raise _ArgumentFailure
        return GenerationRecord(mode="fixture")
    if options.provider != "local" or not all(local_flags):
        raise _ArgumentFailure
    return GenerationRecord(
        mode="local",
        local=LocalInjectionConfiguration(
            base_url=options.base_url,
            model=options.model,
            weights_hash=options.weights_sha256,
        ),
        server_version=options.server_version,
    )


def _run(
    options: argparse.Namespace, generation: GenerationRecord
) -> tuple[int, dict[str, Any]]:
    from app.audits.isolation_reports import IsolationStrategy
    from app.audits.seeding import seed_workspace
    from app.audits.workspace import AuditConfiguration, AuditWorkspace
    from app.evaluation.dataset import generate_utility_corpus
    from app.evaluation.models import PinnedEmbeddingProvider, load_embedding_pins
    from app.evaluation.reports import (
        UtilityDocumentBinding,
        UtilityQueryRecord,
        UtilityReport,
        build_utility_report,
        write_utility_report,
    )
    from app.evaluation.runner import (
        UtilityExecution,
        execute_utility_queries,
        utility_document_bindings,
    )
    from app.evaluation.workspace import utility_template

    strategy = cast(IsolationStrategy, options.strategy)
    try:
        config = AuditConfiguration.model_validate(
            _configuration().model_dump()
            | {
                "embedding_fingerprint": load_embedding_pins().fingerprint,
                "embedding_dimension": 384,
                "vector_strategy": "tenant_collections"
                if strategy == "tenant_collections"
                else "shared_pre_filter",
            }
        )
    except Exception:
        return _diagnostic("utility_invalid_configuration", 2)
    try:
        embeddings = PinnedEmbeddingProvider(options.embedding_root)
    except (Exception, KeyboardInterrupt):
        return _diagnostic("utility_model_failed", 2)
    corpus = generate_utility_corpus()
    template = utility_template(corpus)
    run_id = uuid4()
    execution = UtilityExecution((), False, False)
    provenance: BenchmarkProvenance | None = None
    bindings: tuple[UtilityDocumentBinding, ...] = ()
    collections: tuple[str, ...] = ()
    opened = ready = False

    def publish(item: UtilityReport, directory: Path) -> Path:
        config.validate_paths()
        content = item.model_dump_json()
        if any(
            secret in content
            for secret in (
                config.application_password.get_secret_value(),
                config.fixture_password.get_secret_value(),
                "AUDITCANARY",
                "FACTANSWER",
                "Bearer ",
            )
        ):
            raise ValueError("unsafe_utility_artifact")
        return write_utility_report(item, directory)

    def checkpoint(records: tuple[UtilityQueryRecord, ...]) -> None:
        nonlocal execution
        execution = UtilityExecution(records, execution.runtime_failed, False)
        if provenance is None:
            raise ValueError("utility_metadata_failed")
        snapshot = build_utility_report(
            provenance,
            bindings,
            records,
            strategy=strategy,
            collection_names=collections,
            provisional=True,
        )
        publish(snapshot, config.report_dir / "utility-checkpoints" / run_id.hex)

    try:
        with AuditWorkspace(config, template, embeddings=embeddings) as workspace:
            opened = True
            seed_workspace(workspace, template)
            bindings = utility_document_bindings(workspace, corpus)
            collections = workspace.store.collection_names()
            provenance = collect_provenance(workspace, generation)
            ready = True
            execution = execute_utility_queries(
                workspace,
                corpus,
                generation,
                strategy=strategy,
                lab=options.lab,
                on_record=checkpoint,
            )
    except (Exception, KeyboardInterrupt) as error:
        if not opened:
            return _diagnostic("utility_workspace_failed", 2)
        if not ready:
            return _diagnostic("utility_preparation_failed", 2)
        execution = UtilityExecution(
            execution.records, True, isinstance(error, KeyboardInterrupt)
        )
    if provenance is None:
        return _diagnostic("utility_metadata_failed", 2)
    try:
        report = build_utility_report(
            provenance,
            bindings,
            execution.records,
            strategy=strategy,
            collection_names=collections,
            runtime_failed=execution.runtime_failed,
        ).model_copy(update={"run_id": run_id})
        publish(report, config.report_dir)
    except (Exception, KeyboardInterrupt):
        return _diagnostic("utility_report_write_failed", 2)
    return report.exit_code, {
        "code": "utility_runtime_failed"
        if execution.runtime_failed
        else "utility_incomplete"
        if report.exit_code == 2
        else "utility_complete",
        "exit_code": report.exit_code,
        "run_id": str(run_id),
        "query_count": len(execution.records),
        "strategy": strategy,
        "provider_mode": generation.mode,
    }


def _offline(options: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    from app.evaluation.reports import compare_utility_reports, validate_utility_report

    try:
        if options.validate_report is not None:
            report = validate_utility_report(options.validate_report)
            return 0, {
                "code": "utility_artifact_valid",
                "exit_code": 0,
                "run_exit_code": report.exit_code,
                "coverage_complete": report.coverage_complete,
            }
        first, second = options.compare_reports
        comparison = compare_utility_reports(
            validate_utility_report(first), validate_utility_report(second)
        )
        return 0, {
            "code": "utility_comparison_complete",
            "exit_code": 0,
            "comparison": comparison.model_dump(mode="json"),
        }
    except (Exception, KeyboardInterrupt):
        return _diagnostic("utility_artifact_validation_failed", 2)


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(
        prog="ragelit-benchmark",
        description="Measure utility through an owned local document assistant.",
    )
    parser.add_argument(
        "--strategy",
        choices=("shared_pre_filter", "tenant_collections", "lab_post_filter"),
    )
    parser.add_argument("--lab", action="store_true")
    parser.add_argument("--embedding-root", type=Path)
    parser.add_argument("--provider", choices=("fixture", "local"))
    parser.add_argument("--base-url")
    parser.add_argument("--model")
    parser.add_argument("--weights-sha256")
    parser.add_argument("--server-version")
    offline = parser.add_mutually_exclusive_group()
    offline.add_argument("--validate-report", type=Path)
    offline.add_argument("--compare-reports", type=Path, nargs=2)
    try:
        options = parser.parse_args(argv)
        validating = (
            options.validate_report is not None or options.compare_reports is not None
        )
        if validating:
            if (
                any(
                    getattr(options, field) is not None
                    for field in (
                        "strategy",
                        "embedding_root",
                        "provider",
                        "base_url",
                        "model",
                        "weights_sha256",
                        "server_version",
                    )
                )
                or options.lab
            ):
                raise _ArgumentFailure
        else:
            if (
                options.embedding_root is None
                or options.provider is None
                or options.lab != (options.strategy == "lab_post_filter")
            ):
                raise _ArgumentFailure
            generation = _generation(options)
            options.strategy = options.strategy or "shared_pre_filter"
    except _ParserExit as stopped:
        return stopped.status
    except Exception:
        code, output = _diagnostic("utility_invalid_arguments", 2)
    else:
        try:
            with _quiet_dependencies():
                code, output = (
                    _offline(options) if validating else _run(options, generation)
                )
        except (Exception, KeyboardInterrupt):
            code, output = _diagnostic("utility_runtime_failed", 2)
    print(json.dumps(output, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
