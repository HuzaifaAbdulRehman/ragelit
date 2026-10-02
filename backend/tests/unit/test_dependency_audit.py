import json
import runpy
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def audit_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("OSV_SCANNER", raising=False)
    for name in (
        "backend/uv.lock",
        "frontend/package-lock.json",
        "db/osv-scalibr/PyPI/all.zip",
        "db/osv-scalibr/npm/all.zip",
    ):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "backend/uv.lock").write_text(
        'version = 1\n[[package]]\nname = "fixture"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    (tmp_path / "frontend/package-lock.json").write_text(
        json.dumps({"packages": {"node_modules/fixture": {"version": "1.0.0"}}}),
        encoding="utf-8",
    )
    for ecosystem in ("PyPI", "npm"):
        with zipfile.ZipFile(
            tmp_path / "db/osv-scalibr" / ecosystem / "all.zip", "w"
        ) as archive:
            archive.writestr(
                "fixture.json",
                json.dumps(
                    {
                        "id": "TEST-001",
                        "modified": "2026-01-01T00:00:00Z",
                        "affected": [],
                    }
                ),
            )
    monkeypatch.setenv("OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY", str(tmp_path / "db"))
    return tmp_path


def audit_main() -> Any:
    script = Path(__file__).resolve().parents[3] / "scripts/audit_dependencies.py"
    assert script.is_file(), "The offline audit entry point is missing"
    return runpy.run_path(str(script))["main"]


@pytest.mark.parametrize("scanner_status", [0, 1, 128])
def test_audit_preserves_scanner_outcome(
    audit_inputs: Path, monkeypatch: pytest.MonkeyPatch, scanner_status: int
) -> None:
    def scan(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert command == [
            "osv-scanner",
            "scan",
            "source",
            "--offline",
            "--local-db-path",
            str(audit_inputs / "db"),
            "--lockfile",
            str(audit_inputs / "backend/uv.lock"),
            "--lockfile",
            str(audit_inputs / "frontend/package-lock.json"),
        ]
        assert kwargs["cwd"] == audit_inputs
        return subprocess.CompletedProcess(command, scanner_status)

    monkeypatch.setattr(subprocess, "run", scan)
    assert audit_main()(["--repo-root", str(audit_inputs)]) == scanner_status


@pytest.mark.parametrize(
    "missing",
    [
        "backend/uv.lock",
        "frontend/package-lock.json",
        "db/osv-scalibr/PyPI/all.zip",
        "db/osv-scalibr/npm/all.zip",
    ],
)
def test_audit_rejects_missing_inputs(audit_inputs: Path, missing: str) -> None:
    (audit_inputs / missing).unlink()
    assert audit_main()(["--repo-root", str(audit_inputs)]) == 2


def test_audit_requires_explicit_database_directory(
    audit_inputs: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY")
    assert audit_main()(["--repo-root", str(audit_inputs)]) == 2


def test_audit_rejects_unavailable_scanner(
    audit_inputs: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OSV_SCANNER", str(audit_inputs / "missing-scanner"))
    assert audit_main()(["--repo-root", str(audit_inputs)]) == 2
