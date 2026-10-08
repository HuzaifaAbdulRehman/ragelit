import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from qdrant_client import QdrantClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.audits.fixtures import generate_fixtures
from app.audits.isolation_reports import (
    validate_isolation_release_directory,
    validate_isolation_report,
)
from app.audits.workspace import AuditConfiguration, FixtureBindings
from tests.integration.audits.support import audit_config as audit_config
from tests.integration.audits.test_cli import cli_environment


@pytest.fixture
def workspaces(
    audit_config: AuditConfiguration,
) -> Iterator[tuple[AuditConfiguration, ...]]:
    configurations = tuple(
        AuditConfiguration.model_validate(
            audit_config.model_dump()
            | {
                "database_admin_url": make_url(
                    audit_config.database_admin_url.get_secret_value()
                )
                .set(database=name)
                .render_as_string(hide_password=False),
                "root": audit_config.root.parent / name,
                "vector_strategy": "tenant_collections"
                if index == 1
                else "shared_pre_filter",
            }
        )
        for index, name in enumerate(f"ragelit_audit_{uuid4().hex}" for _ in range(3))
    )
    server = create_engine(
        audit_config.admin_url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        hide_parameters=True,
    )
    client = QdrantClient(url=audit_config.qdrant_url, trust_env=False)
    try:
        with server.connect() as connection:
            for config in configurations:
                assert not config.root.exists()
                assert (
                    connection.execute(
                        text("SELECT 1 FROM pg_database WHERE datname = :name"),
                        {"name": config.name},
                    ).scalar_one_or_none()
                    is None
                )
                assert (
                    connection.execute(
                        text("SELECT 1 FROM pg_roles WHERE rolname = :name"),
                        {"name": config.application_role},
                    ).scalar_one_or_none()
                    is None
                )
                assert not any(
                    item.name == config.name
                    or item.name.startswith(f"{config.name}_tenant_")
                    for item in client.get_collections().collections
                )
        try:
            yield configurations
        finally:
            for config in configurations:
                config.validate_paths()
                assert config.name.startswith("ragelit_audit_")
                names: tuple[str, ...] = (
                    (config.name,)
                    if config.vector_strategy == "shared_pre_filter"
                    else ()
                )
                if config.bindings_path.is_file():
                    bindings = FixtureBindings.model_validate_json(
                        config.bindings_path.read_bytes()
                    )
                    assert bindings.workspace_id == config.workspace_id
                    assert bindings.template_hash == generate_fixtures().checksum
                    if config.vector_strategy == "tenant_collections":
                        names = tuple(
                            f"{config.name}_tenant_{value.hex}"
                            for value in bindings.organizations.values()
                        )
                for name in names:
                    if client.collection_exists(name):
                        client.delete_collection(name)
                with server.connect() as connection:
                    connection.execute(
                        text(f'DROP DATABASE IF EXISTS "{config.name}" WITH (FORCE)')
                    )
                    connection.execute(
                        text(f'DROP ROLE IF EXISTS "{config.application_role}"')
                    )
    finally:
        client.close()
        server.dispose()


def test_real_cli_exports_three_fresh_workspace_strategies(
    workspaces: tuple[AuditConfiguration, ...], tmp_path: Path
) -> None:
    export = tmp_path / "isolation-release"
    export.mkdir()
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
    lock_hashes = tuple(
        hashlib.sha256((root / filename).read_bytes()).hexdigest()
        for filename in ("backend/uv.lock", "frontend/package-lock.json")
    )
    for config, strategy, gate, collection_count in zip(
        workspaces,
        ("shared_pre_filter", "tenant_collections", "lab_post_filter"),
        (0, 0, 1),
        (1, 3, 1),
        strict=True,
    ):
        arguments = ["--strategy", strategy] + (
            ["--lab"] if strategy == "lab_post_filter" else []
        )
        process = subprocess.run(
            [sys.executable, "-m", "app.audits.isolation_cli", *arguments],
            env=cli_environment(config),
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        assert process.returncode == gate, process.stdout + process.stderr
        assert process.stderr == ""
        output = json.loads(process.stdout)
        assert output["code"] == "isolation_complete"
        assert output["exit_code"] == gate and output["case_count"] == 51
        assert output["strategy"] == strategy
        path = config.report_dir / f"{UUID(output['run_id'])}.json"
        report = validate_isolation_report(path)
        assert report.strategy == strategy and report.audit.exit_code == gate
        assert report.audit.coverage_complete and len(report.audit.results) == 51
        assert len(report.collections) == collection_count
        bindings = FixtureBindings.model_validate_json(
            config.bindings_path.read_bytes()
        )
        assert report.audit.metadata.binding_hash == bindings.checksum
        assert report.audit.metadata.config_hash == config.config_hash
        assert report.audit.metadata.git_revision == revision
        assert report.audit.metadata.git_dirty == dirty
        assert report.audit.metadata.lock_hashes == lock_hashes
        assert report.collections == (
            tuple(
                sorted(
                    f"{config.name}_tenant_{value.hex}"
                    for value in bindings.organizations.values()
                )
            )
            if strategy == "tenant_collections"
            else (config.name,)
        )
        content = path.read_bytes().decode()
        for secret in (
            config.application_password.get_secret_value(),
            config.fixture_password.get_secret_value(),
            "AUDITCANARY",
            "Bearer ",
            "audittopic",
            "audit-anchor",
            "postgresql+psycopg://",
        ):
            assert secret not in content and secret not in process.stdout
        shutil.copyfile(path, export / path.name)
        receipt = path.with_suffix(".sha256.json")
        shutil.copyfile(receipt, export / receipt.name)
    assert len(validate_isolation_release_directory(export)) == 3
    validated = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.audits.isolation_cli",
            "--validate-reports",
            str(export),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert validated.returncode == 0 and validated.stderr == ""
    assert json.loads(validated.stdout) == {
        "code": "isolation_artifacts_valid",
        "exit_code": 0,
    }
    if destination := os.environ.get("RAGELIT_ISOLATION_EXPORT_DIRECTORY"):
        directory = Path(destination).absolute()
        assert not directory.exists() and not directory.is_symlink()
        assert not any(
            part.is_symlink() or part.is_junction() for part in directory.parents
        )
        directory.mkdir(parents=True)
        for artifact in export.iterdir():
            shutil.copyfile(artifact, directory / artifact.name)


def test_real_cli_lab_opt_in_is_checked_before_configuration_or_workspace(
    audit_config: AuditConfiguration,
) -> None:
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.audits.isolation_cli",
            "--strategy",
            "lab_post_filter",
        ],
        env=cli_environment(audit_config),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert process.returncode == 2 and process.stderr == ""
    assert json.loads(process.stdout) == {
        "code": "isolation_invalid_arguments",
        "exit_code": 2,
    }
    assert not audit_config.root.exists()
