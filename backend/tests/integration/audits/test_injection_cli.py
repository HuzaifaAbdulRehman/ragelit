import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import UUID

from app.audits.contracts import Boundary
from app.audits.injection_reports import (
    validate_injection_release_directory,
    validate_injection_report,
)
from app.audits.workspace import AuditConfiguration, FixtureBindings
from tests.integration.audits.support import audit_config as audit_config
from tests.integration.audits.test_cli import cli_environment
from tests.unit.chat.test_provider import endpoint


def test_injection_cli_exports_real_three_profile_release(
    audit_config: AuditConfiguration, tmp_path: Path
) -> None:
    export = tmp_path / "injection-release"
    export.mkdir()
    for profile, gate, successes in (
        ("resistant", 0, 0),
        ("obeying", 1, 3),
        ("deny_all", 1, 0),
    ):
        process = subprocess.run(
            [sys.executable, "-m", "app.audits.injection_cli", "--profile", profile],
            env=cli_environment(audit_config),
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        assert process.returncode == gate, process.stdout + process.stderr
        assert process.stderr == ""
        output = json.loads(process.stdout)
        assert output["code"] == "injection_complete"
        assert output["exit_code"] == gate
        assert output["case_count"] == 6
        path = audit_config.report_dir / f"{UUID(output['run_id'])}.json"
        report = validate_injection_report(path)
        assert report.provider.mode == profile
        assert report.summary.exit_code == gate
        assert report.summary.coverage_complete
        assert report.summary.evaluated_successes == successes
        assert report.summary.evaluated_attacks == (0 if profile == "deny_all" else 3)
        assert report.summary.benign_controls_passed == (profile != "deny_all")
        if profile == "obeying":
            assert report.summary.observed_signals == 3
            assert all(
                result.first_instruction_boundary == Boundary.OUTPUT_CANDIDATE
                for result in report.results
                if ":attack:" in result.case_id
            )
        bindings = FixtureBindings.model_validate_json(
            audit_config.bindings_path.read_bytes()
        )
        assert report.metadata.binding_hash == bindings.checksum
        assert report.documents == bindings.documents
        assert report.metadata.config_hash == audit_config.config_hash
        root = Path(__file__).resolve().parents[4]
        assert (
            report.metadata.git_revision
            == subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True
            ).strip()
        )
        assert report.metadata.lock_hashes == tuple(
            hashlib.sha256((root / filename).read_bytes()).hexdigest()
            for filename in ("backend/uv.lock", "frontend/package-lock.json")
        )
        content = path.read_bytes().decode()
        for secret in (
            audit_config.application_password.get_secret_value(),
            audit_config.fixture_password.get_secret_value(),
            "FACTANSWER",
            "AUDITCANARY",
            "Bearer ",
            "audittopic",
            "audit-anchor",
        ):
            assert secret not in content
            assert secret not in process.stdout
        shutil.copyfile(path, export / path.name)
        receipt = path.with_suffix(".sha256.json")
        shutil.copyfile(receipt, export / receipt.name)
    checked = validate_injection_release_directory(export)
    assert len(checked) == 3
    if destination := os.environ.get("RAGELIT_INJECTION_EXPORT_DIRECTORY"):
        directory = Path(destination)
        directory.mkdir(parents=True, exist_ok=True)
        for artifact in export.iterdir():
            shutil.copyfile(artifact, directory / artifact.name)


def test_local_provider_failure_is_inconclusive_and_does_not_use_app_key(
    audit_config: AuditConfiguration,
) -> None:
    environment = cli_environment(audit_config)
    environment["RAGELIT_LLM_API_KEY"] = "OrdinaryProviderKeyNotAllowed"
    with endpoint(b"SensitiveProviderErrorNeverEcho", status=503) as (url, requests):
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "app.audits.injection_cli",
                "--profile",
                "local",
                "--local-base-url",
                url,
                "--model",
                "owned-error-fixture",
                "--weights-sha256",
                "a" * 64,
            ],
            env=environment,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        assert process.returncode == 2, process.stdout + process.stderr
        output = json.loads(process.stdout)
        assert output["code"] == "injection_incomplete"
        assert output["case_count"] == 6
        assert len(requests) == 6
        for request in requests:
            assert "Authorization" not in request["headers"]
            assert request["body"]["model"] == "owned-error-fixture"
            assert request["body"]["temperature"] == 0
            assert request["body"]["max_tokens"] == 1024
        report = validate_injection_report(
            audit_config.report_dir / f"{UUID(output['run_id'])}.json"
        )
        assert report.provider.mode == "local"
        assert report.provider.local is not None
        assert report.provider.local.weights_hash == "a" * 64
        assert report.summary.exit_code == 2
        assert report.summary.evaluated_attacks == 0
        assert report.summary.attack_success_rate is None
        assert report.summary.incomplete_cases == 6
        assert not report.summary.benign_controls_passed
        captured = process.stdout + process.stderr + report.model_dump_json()
        assert "OrdinaryProviderKeyNotAllowed" not in captured
        assert "SensitiveProviderErrorNeverEcho" not in captured
        assert process.stderr == ""
