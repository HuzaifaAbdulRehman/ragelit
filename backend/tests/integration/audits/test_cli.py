import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from app.audits.fixtures import generate_fixtures
from app.audits.reports import AuditReport
from app.audits.seeding import seed_workspace
from app.audits.workspace import AuditConfiguration, AuditWorkspace, FixtureBindings
from tests.integration.audits.support import audit_config as audit_config


def cli_environment(config: AuditConfiguration) -> dict[str, str]:
    values = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("RAGELIT_AUDIT_")
    }
    values.update(
        {
            "RAGELIT_AUDIT_ENVIRONMENT": config.environment,
            "RAGELIT_AUDIT_DATABASE_ADMIN_URL": (
                config.database_admin_url.get_secret_value()
            ),
            "RAGELIT_AUDIT_QDRANT_URL": config.qdrant_url,
            "RAGELIT_AUDIT_ROOT": str(config.root),
            "RAGELIT_AUDIT_APPLICATION_PASSWORD": (
                config.application_password.get_secret_value()
            ),
            "RAGELIT_AUDIT_FIXTURE_PASSWORD": (
                config.fixture_password.get_secret_value()
            ),
        }
    )
    return values


def invoke(
    config: AuditConfiguration, arguments: list[str]
) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        [sys.executable, "-m", "app.audits.cli", *arguments],
        env=cli_environment(config),
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    captured = process.stdout + process.stderr
    for secret in (
        config.application_password.get_secret_value(),
        config.fixture_password.get_secret_value(),
        "AUDITCANARY",
        "Bearer ",
        "postgresql+psycopg://",
        "http://",
    ):
        assert secret not in captured
    assert process.stderr == ""
    return process


def report_from_output(
    config: AuditConfiguration, process: subprocess.CompletedProcess[str]
) -> tuple[AuditReport, Path]:
    output = json.loads(process.stdout)
    run_id = UUID(output["run_id"])
    path = config.report_dir / f"{run_id}.json"
    content = path.read_bytes()
    report = AuditReport.model_validate_json(content)
    assert report.run_id == run_id
    receipt = json.loads(path.with_suffix(".sha256.json").read_bytes())
    assert receipt == {
        "filename": path.name,
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    for secret in (
        config.application_password.get_secret_value(),
        config.fixture_password.get_secret_value(),
        "AUDITCANARY",
        "Bearer ",
        "audittopic",
        "audit-anchor",
    ):
        assert secret not in content.decode()
    return report, path


@pytest.mark.parametrize(
    "profile,expected", [("safe", 0), ("vulnerable", 1), ("deny_all", 1)]
)
def test_cli_full_profile_has_literal_gate_and_safe_artifacts(
    audit_config: AuditConfiguration, profile: str, expected: int
) -> None:
    arguments = [] if profile == "safe" else ["--profile", profile, "--lab"]
    process = invoke(audit_config, arguments)
    assert process.returncode == expected
    report, path = report_from_output(audit_config, process)
    assert report.exit_code == expected
    assert report.coverage_complete
    assert len(report.required_case_ids) == len(report.results) == 51
    assert report.metadata.profile == profile
    assert report.metadata.pack_id == "access-control-v1"
    assert report.metadata.generator_id == "synthetic-fixtures-v1"
    assert report.metadata.embedding_id == "fixture-topic-v1"
    assert report.metadata.provider_id == "fixture-citing-v1"
    assert (
        report.metadata.template_hash
        == "4949502da16d689928823c2ecdc22647423f92fbf6a317e2856cefa6d51e0277"
    )
    root = Path(__file__).resolve().parents[4]
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    dirty = bool(
        subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=root,
            text=True,
        ).strip()
    )
    assert report.metadata.git_revision == revision
    assert report.metadata.git_dirty == dirty
    assert report.metadata.lock_hashes == tuple(
        hashlib.sha256((root / filename).read_bytes()).hexdigest()
        for filename in ("backend/uv.lock", "frontend/package-lock.json")
    )
    bindings = FixtureBindings.model_validate_json(
        audit_config.bindings_path.read_bytes()
    )
    assert report.metadata.binding_hash == bindings.checksum
    assert report.metadata.config_hash == audit_config.config_hash
    assert {key.split(":", 1)[0] for key in bindings.instances} == {str(report.run_id)}
    if export := os.environ.get("RAGELIT_AUDIT_EXPORT_DIRECTORY"):
        destination = Path(export)
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination / path.name)
        receipt = path.with_suffix(".sha256.json")
        shutil.copyfile(receipt, destination / receipt.name)


def test_partial_selection_keeps_the_full_inventory_and_returns_two(
    audit_config: AuditConfiguration,
) -> None:
    process = invoke(audit_config, ["--case", "org-1:organization"])
    assert process.returncode == 2
    report, _ = report_from_output(audit_config, process)
    assert report.exit_code == 2
    assert not report.coverage_complete
    assert len(report.required_case_ids) == 51
    assert [result.case_id for result in report.results] == ["org-1:organization"]
    assert report.results[0].status == "pass"
    bindings = FixtureBindings.model_validate_json(
        audit_config.bindings_path.read_bytes()
    )
    assert len(bindings.instances) == 18
    assert {instance.state for instance in bindings.instances.values()} == {"skipped"}
    process = invoke(audit_config, [])
    assert process.returncode == 0
    repeated, _ = report_from_output(audit_config, process)
    assert repeated.coverage_complete and repeated.exit_code == 0
    assert len(repeated.results) == 51
    bindings = FixtureBindings.model_validate_json(
        audit_config.bindings_path.read_bytes()
    )
    states = [instance.state for instance in bindings.instances.values()]
    assert states.count("skipped") == states.count("complete") == 18


def test_unavailable_service_returns_two_without_unsafe_diagnostics(
    tmp_path: Path,
) -> None:
    name = f"ragelit_audit_{uuid4().hex}"
    config = AuditConfiguration(
        environment="test",
        database_admin_url=SecretStr(
            f"postgresql+psycopg://postgres:postgres@127.0.0.1:9/{name}?connect_timeout=1"
        ),
        qdrant_url="http://127.0.0.1:6333",
        root=tmp_path / name,
        application_password=SecretStr("SyntheticApplicationPasswordMarker"),
        fixture_password=SecretStr("SyntheticFixturePasswordMarker"),
    )
    process = invoke(config, [])
    assert process.returncode == 2
    assert json.loads(process.stdout) == {
        "code": "audit_workspace_failed",
        "exit_code": 2,
    }
    assert not config.root.exists()


def test_report_destination_failure_is_not_a_passing_gate(
    audit_config: AuditConfiguration,
) -> None:
    with AuditWorkspace(audit_config, generate_fixtures()) as workspace:
        seed_workspace(workspace, workspace.template)
    audit_config.report_dir.write_text("blocked destination", encoding="utf-8")
    process = invoke(audit_config, ["--case", "org-1:organization"])
    assert process.returncode == 2
    assert json.loads(process.stdout) == {
        "code": "audit_report_write_failed",
        "exit_code": 2,
    }
    assert audit_config.report_dir.read_text(encoding="utf-8") == "blocked destination"
