import os
import subprocess
from pathlib import Path

import pytest
from psycopg._conninfo_attempts import conninfo_attempts
from psycopg._conninfo_utils import get_param
from pydantic import ValidationError
from sqlalchemy import create_engine

from app.audits.workspace import AuditConfiguration, AuditWorkspaceError


def configuration(parent: Path, **changes: object) -> AuditConfiguration:
    return AuditConfiguration.model_validate(
        {
            "environment": "test",
            "database_admin_url": "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/ragelit_audit_unit",
            "qdrant_url": "http://127.0.0.1:6333",
            "root": parent / "ragelit_audit_unit",
            "application_password": "AuditApplicationPasswordMarker",
            "fixture_password": "AuditFixturePasswordMarker",
        }
        | changes
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"environment": "production"},
        {"qdrant_timeout_seconds": 0},
        {"qdrant_timeout_seconds": 31},
        {
            "database_admin_url": "postgresql+psycopg://u:p@db.example.invalid/ragelit_audit_unit"
        },
        {"database_admin_url": "postgresql+psycopg://u:p@127.0.0.1/ragelit"},
        {
            "database_admin_url": "postgresql+psycopg://u:p@127.0.0.1/ragelit_audit_unit?host=remote"
        },
        {
            "database_admin_url": "postgresql+psycopg://u:p@127.0.0.1/ragelit_audit_unit?options=-c%20role=postgres"
        },
        {"database_admin_url": "sqlite:///ragelit_audit_unit"},
        {"qdrant_url": "http://qdrant.example.invalid:6333"},
        {"qdrant_url": "http://127.0.0.1:6333/redirect"},
        {"qdrant_url": "http://u:p@127.0.0.1:6333"},
        {"qdrant_url": "http://127.0.0.1:6333?target=remote"},
        {"qdrant_url": "http://0.0.0.0:6333"},
    ],
)
def test_configuration_refuses_non_audit_or_redirectable_targets(
    tmp_path: Path,
    changes: dict[str, object],
) -> None:
    with pytest.raises((ValidationError, AuditWorkspaceError)):
        configuration(tmp_path, **changes)
    assert not (tmp_path / "ragelit_audit_unit").exists()


@pytest.mark.parametrize("path", [".", "../ragelit_audit_unit", "ordinary-data"])
def test_configuration_refuses_relative_or_ordinary_storage(
    tmp_path: Path, path: str
) -> None:
    root = Path(path) if path != "ordinary-data" else tmp_path / path
    with pytest.raises((ValidationError, AuditWorkspaceError)):
        configuration(tmp_path, root=root)


def test_configuration_revalidates_a_linked_storage_parent(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    target = tmp_path / "outside"
    target.mkdir()
    config = configuration(parent)
    parent.rmdir()
    try:
        parent.symlink_to(target, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(parent), str(target)],
            check=True,
            capture_output=True,
        )
    with pytest.raises(AuditWorkspaceError, match="unsafe_audit_path"):
        config.validate_paths()
    assert not (target / "ragelit_audit_unit").exists()


def test_config_hash_excludes_credentials_and_keeps_target_identity(
    tmp_path: Path,
) -> None:
    first = configuration(tmp_path)
    second = configuration(
        tmp_path,
        application_password="DifferentApplicationPassword",
        fixture_password="DifferentFixturePassword",
        database_admin_url="postgresql+psycopg://another:secret@127.0.0.1:5432/ragelit_audit_unit",
    )
    assert first.config_hash == second.config_hash
    assert first.workspace_id == second.workspace_id
    assert (
        first.config_hash
        != configuration(tmp_path, qdrant_url="http://127.0.0.1:6334").config_hash
    )
    assert "AuditApplicationPasswordMarker" not in repr(first)
    assert "AuditFixturePasswordMarker" not in repr(first)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_database_connections_ignore_ambient_destination_and_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    monkeypatch.setenv("PGHOSTADDR", "203.0.113.10")
    monkeypatch.setenv("PGPORT", "6543")
    monkeypatch.setenv("PGCONNECT_TIMEOUT", "600")
    authority = f"[{host}]" if ":" in host else host
    config = configuration(
        tmp_path,
        database_admin_url=f"postgresql+psycopg://postgres:postgres@{authority}/ragelit_audit_unit",
    )
    for url in (
        config.admin_url.set(database="postgres"),
        config.admin_url,
        config.application_url,
    ):
        engine = create_engine(url)
        try:
            _, parameters = engine.dialect.create_connect_args(engine.url)
            attempts = conninfo_attempts(parameters)
            assert len(attempts) == 1
            assert get_param(attempts[0], "hostaddr") == host
            assert get_param(attempts[0], "port") == "5432"
            assert get_param(attempts[0], "connect_timeout") == "5"
        finally:
            engine.dispose()
    assert not config.root.exists()
