from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any
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
    from app.audits.injection_reports import InjectionProviderRecord
    from app.audits.injection_scoring import InjectionCase, InjectionResult
    from app.audits.target import BundledAuditTarget
    from app.chat.contracts import GenerationProvider


def execute_injection_cases(
    target: BundledAuditTarget, cases: tuple[InjectionCase, ...]
) -> tuple[tuple[InjectionResult, ...], bool]:
    from app.audits.injection_scoring import score_injection

    results = []
    for control in cases:
        try:
            observed = target.execute(control.access_case)
        except (Exception, KeyboardInterrupt):
            last = target.last_observation
            if last is not None and last.case_id == control.access_case.id:
                results.append(score_injection(control, last))
            return tuple(results), True
        results.append(score_injection(control, observed))
    return tuple(results), False


def _provider(options: argparse.Namespace) -> InjectionProviderRecord:
    from app.audits.injection_providers import LocalInjectionConfiguration
    from app.audits.injection_reports import InjectionProviderRecord

    values = (options.local_base_url, options.model, options.weights_sha256)
    if options.profile == "local":
        if any(value is None for value in values):
            raise _ArgumentFailure
        local = LocalInjectionConfiguration(
            base_url=options.local_base_url,
            model=options.model,
            weights_hash=options.weights_sha256,
        )
        return InjectionProviderRecord(mode="local", local=local)
    if any(value is not None for value in values):
        raise _ArgumentFailure
    return InjectionProviderRecord(mode=options.profile or "resistant")


def _run(
    options: argparse.Namespace, provider: InjectionProviderRecord
) -> tuple[int, dict[str, Any]]:
    from app.audits.contracts import RunMetadata
    from app.audits.embeddings import FixtureEmbeddings
    from app.audits.injection_fixtures import (
        generate_injection_fixtures,
        injection_cases,
        prepare_injection_pack,
    )
    from app.audits.injection_providers import InjectionProvider
    from app.audits.injection_reports import (
        build_injection_report,
        expected_injection_cases,
        write_injection_report,
    )
    from app.audits.target import BundledAuditTarget
    from app.audits.workspace import AuditWorkspace, FixtureBindings

    trials = options.trials or 1
    template = generate_injection_fixtures(trials=trials)
    try:
        config = _configuration()
    except Exception:
        return _diagnostic("injection_invalid_configuration", 2)
    try:
        repository = _repository_metadata()
        provenance = repository | {
            "profile": "safe",
            "pack_id": template.pack_id,
            "generator_id": template.generator_id,
            "embedding_id": FixtureEmbeddings.identifier,
            "provider_id": provider.identifier,
            "template_hash": template.checksum,
            "config_hash": config.config_hash,
        }
        RunMetadata.model_validate(provenance | {"binding_hash": "0" * 64})
    except Exception:
        return _diagnostic("injection_metadata_failed", 2)
    run_id = uuid4()
    results: tuple[InjectionResult, ...] = ()
    bindings: FixtureBindings | None = None
    opened = ready = runtime_failed = False
    try:
        with AuditWorkspace(config, template) as workspace:
            opened = True
            try:
                pack = prepare_injection_pack(workspace, run_id)
                cases = injection_cases(pack, template)
                if cases != expected_injection_cases(
                    workspace.bindings.documents, trials=trials
                ):
                    raise ValueError("injection_inventory_mismatch")
                ready = True
                generation: GenerationProvider
                if provider.mode == "local":
                    assert provider.local is not None
                    generation = provider.local.provider(workspace.settings)
                else:
                    generation = InjectionProvider(provider.mode)
                target = BundledAuditTarget(
                    workspace, pack, generation_provider=generation
                )
                results, runtime_failed = execute_injection_cases(target, cases)
            except (Exception, KeyboardInterrupt):
                runtime_failed = True
            finally:
                bindings = FixtureBindings.model_validate_json(
                    config.bindings_path.read_bytes()
                )
    except (Exception, KeyboardInterrupt):
        if not opened:
            return _diagnostic("injection_workspace_failed", 2)
        runtime_failed = True
    if not ready:
        return _diagnostic("injection_preparation_failed", 2)
    if (
        bindings is None
        or bindings.workspace_id != config.workspace_id
        or bindings.template_hash != template.checksum
    ):
        return _diagnostic("injection_metadata_failed", 2)
    try:
        metadata = RunMetadata.model_validate(
            provenance | {"binding_hash": bindings.checksum}
        )
        report = build_injection_report(
            results,
            metadata,
            documents=bindings.documents,
            provider=provider,
            trials=trials,
            runtime_failed=runtime_failed,
        ).model_copy(update={"run_id": run_id})
        config.validate_paths()
        content = report.model_dump_json()
        if any(
            secret in content
            for secret in (
                config.application_password.get_secret_value(),
                config.fixture_password.get_secret_value(),
                "FACTANSWER",
                "AUDITCANARY",
                "Bearer ",
            )
        ):
            raise ValueError("unsafe_injection_artifact")
        write_injection_report(report, config.report_dir)
    except (Exception, KeyboardInterrupt):
        return _diagnostic("injection_report_write_failed", 2)
    gate = report.summary.exit_code
    return gate, {
        "code": "injection_runtime_failed"
        if runtime_failed
        else "injection_incomplete"
        if gate == 2
        else "injection_complete",
        "exit_code": gate,
        "run_id": str(run_id),
        "case_count": len(results),
    }


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(
        prog="ragelit-injection-audit",
        description=(
            "Run synthetic document-instruction checks in an owned local workspace."
        ),
    )
    parser.add_argument(
        "--profile", choices=("resistant", "obeying", "deny_all", "local")
    )
    parser.add_argument(
        "--trials", type=int, help="Trials per tenant (1..20, default 1)."
    )
    parser.add_argument(
        "--local-base-url", help="Explicit numeric loopback HTTP /v1 URL."
    )
    parser.add_argument("--model", help="Local model identifier.")
    parser.add_argument("--weights-sha256", help="SHA-256 of the local model weights.")
    validation = parser.add_mutually_exclusive_group()
    validation.add_argument("--validate-report", type=Path)
    validation.add_argument("--validate-reports", type=Path)
    try:
        options = parser.parse_args(argv)
    except _ParserExit as stopped:
        return stopped.status
    except _ArgumentFailure:
        code, output = _diagnostic("injection_invalid_arguments", 2)
    else:
        validating = (
            options.validate_report is not None or options.validate_reports is not None
        )
        invalid = (options.trials is not None and not 1 <= options.trials <= 20) or (
            validating
            and any(
                value is not None
                for value in (
                    options.profile,
                    options.trials,
                    options.local_base_url,
                    options.model,
                    options.weights_sha256,
                )
            )
        )
        if invalid:
            code, output = _diagnostic("injection_invalid_arguments", 2)
        else:
            with _quiet_dependencies():
                if validating:
                    try:
                        from app.audits.injection_reports import (
                            validate_injection_release_directory,
                            validate_injection_report,
                        )

                        if options.validate_report is not None:
                            validate_injection_report(options.validate_report)
                        else:
                            validate_injection_release_directory(
                                options.validate_reports
                            )
                        code, output = _diagnostic("injection_artifacts_valid", 0)
                    except (Exception, KeyboardInterrupt):
                        code, output = _diagnostic(
                            "injection_artifact_validation_failed", 2
                        )
                else:
                    try:
                        provider = _provider(options)
                    except Exception:
                        code, output = _diagnostic("injection_invalid_arguments", 2)
                    else:
                        try:
                            code, output = _run(options, provider)
                        except (Exception, KeyboardInterrupt):
                            code, output = _diagnostic("injection_runtime_failed", 2)
    print(json.dumps(output, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
