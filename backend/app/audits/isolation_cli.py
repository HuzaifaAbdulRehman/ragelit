from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.audits.cli import (
    _ArgumentFailure,
    _configuration,
    _diagnostic,
    _Parser,
    _ParserExit,
    _quiet_dependencies,
    _repository_metadata,
    execute_cases,
)


def _run(options: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    from app.audits.contracts import AuditCaseResult, RunMetadata
    from app.audits.embeddings import FixtureEmbeddings
    from app.audits.fixtures import generate_fixtures
    from app.audits.isolation_lab import LabPostFilterStore
    from app.audits.isolation_reports import (
        IsolationReport,
        IsolationStrategy,
        write_isolation_report,
    )
    from app.audits.reports import build_report
    from app.audits.seeding import FixtureCitingProvider
    from app.audits.target import BundledAuditTarget, prepare_pack
    from app.audits.workspace import AuditConfiguration, AuditWorkspace, FixtureBindings

    strategy: IsolationStrategy = options.strategy or "shared_pre_filter"
    template = generate_fixtures()
    try:
        config = AuditConfiguration.model_validate(
            _configuration().model_dump()
            | {
                "vector_strategy": "tenant_collections"
                if strategy == "tenant_collections"
                else "shared_pre_filter",
            }
        )
    except Exception:
        return _diagnostic("isolation_invalid_configuration", 2)
    try:
        provenance = _repository_metadata() | {
            "profile": "safe",
            "pack_id": template.pack_id,
            "generator_id": template.generator_id,
            "embedding_id": FixtureEmbeddings.identifier,
            "provider_id": FixtureCitingProvider.identifier,
            "template_hash": template.checksum,
            "config_hash": config.config_hash,
        }
        RunMetadata.model_validate(provenance | {"binding_hash": "0" * 64})
    except Exception:
        return _diagnostic("isolation_metadata_failed", 2)
    run_id = uuid4()
    results: tuple[AuditCaseResult, ...] = ()
    bindings: FixtureBindings | None = None
    opened = ready = runtime_failed = False
    try:
        with AuditWorkspace(config, template) as workspace:
            opened = True
            try:
                pack = prepare_pack(workspace, run_id)
                collections = workspace.store.collection_names()
                ready = True
                target = BundledAuditTarget(
                    workspace,
                    pack,
                    lab=strategy == "lab_post_filter",
                    retrieval_store=LabPostFilterStore(workspace, lab=True)
                    if strategy == "lab_post_filter"
                    else None,
                )
                results, runtime_failed = execute_cases(target, pack, None)
            except (Exception, KeyboardInterrupt):
                runtime_failed = True
            finally:
                bindings = FixtureBindings.model_validate_json(
                    config.bindings_path.read_bytes()
                )
    except (Exception, KeyboardInterrupt):
        if not opened:
            return _diagnostic("isolation_workspace_failed", 2)
        runtime_failed = True
    if not ready:
        return _diagnostic("isolation_preparation_failed", 2)
    if (
        bindings is None
        or bindings.workspace_id != config.workspace_id
        or bindings.template_hash != template.checksum
    ):
        return _diagnostic("isolation_metadata_failed", 2)
    try:
        metadata = RunMetadata.model_validate(
            provenance | {"binding_hash": bindings.checksum}
        )
        audit = build_report(
            pack.cases, results, metadata, runtime_failed=runtime_failed
        ).model_copy(update={"run_id": run_id})
        report = IsolationReport(
            strategy=strategy, collections=collections, cases=pack.cases, audit=audit
        )
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
            raise ValueError("unsafe_isolation_artifact")
        write_isolation_report(report, config.report_dir)
    except (Exception, KeyboardInterrupt):
        return _diagnostic("isolation_report_write_failed", 2)
    gate = audit.exit_code
    return gate, {
        "code": "isolation_runtime_failed"
        if runtime_failed
        else "isolation_incomplete"
        if gate == 2
        else "isolation_complete",
        "exit_code": gate,
        "run_id": str(run_id),
        "strategy": strategy,
        "case_count": len(results),
    }


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(
        prog="ragelit-isolation-audit",
        description="Compare retrieval strategies in an owned local audit workspace.",
    )
    parser.add_argument(
        "--strategy",
        choices=("shared_pre_filter", "tenant_collections", "lab_post_filter"),
    )
    parser.add_argument(
        "--lab", action="store_true", help="Allow the lab-only post-filter baseline."
    )
    validation = parser.add_mutually_exclusive_group()
    validation.add_argument("--validate-report", type=Path)
    validation.add_argument("--validate-reports", type=Path)
    try:
        options = parser.parse_args(argv)
    except _ParserExit as stopped:
        return stopped.status
    except _ArgumentFailure:
        code, output = _diagnostic("isolation_invalid_arguments", 2)
    else:
        validating = (
            options.validate_report is not None or options.validate_reports is not None
        )
        if (options.strategy == "lab_post_filter" and not options.lab) or (
            validating and (options.strategy is not None or options.lab)
        ):
            code, output = _diagnostic("isolation_invalid_arguments", 2)
        else:
            with _quiet_dependencies():
                if validating:
                    try:
                        from app.audits.isolation_reports import (
                            validate_isolation_release_directory,
                            validate_isolation_report,
                        )

                        if options.validate_report is not None:
                            validate_isolation_report(options.validate_report)
                        else:
                            validate_isolation_release_directory(
                                options.validate_reports
                            )
                        code, output = _diagnostic("isolation_artifacts_valid", 0)
                    except (Exception, KeyboardInterrupt):
                        code, output = _diagnostic(
                            "isolation_artifact_validation_failed", 2
                        )
                else:
                    try:
                        code, output = _run(options)
                    except (Exception, KeyboardInterrupt):
                        code, output = _diagnostic("isolation_runtime_failed", 2)
    print(json.dumps(output, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
