import json
import os
import shutil
import subprocess
import sys
from importlib import import_module, metadata
from pathlib import Path
from typing import Any, cast

import pytest


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _qdrant_compose_service() -> dict[str, Any]:
    output = subprocess.check_output(
        [
            "docker",
            "compose",
            "--env-file",
            ".env.example",
            "config",
            "--format",
            "json",
            "qdrant",
        ],
        cwd=_repo_root(),
        text=True,
        timeout=30,
    )
    return cast(dict[str, Any], json.loads(output)["services"]["qdrant"])


def test_reference_sources_are_not_tracked() -> None:
    repo_root = _repo_root()
    tracked = subprocess.check_output(
        ["git", "ls-files", "references"], cwd=repo_root, text=True
    )
    assert tracked == ""


def test_third_party_notice_records_template_commit() -> None:
    notice = (_repo_root() / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "cb740b656d7a0a6c5e12c7bf8e50343ec94ee9c7" in notice
    assert "MIT" in notice


def test_postgres_18_volume_mounts_versioned_parent() -> None:
    compose = (_repo_root() / "compose.yml").read_text(encoding="utf-8")

    assert "postgres-data:/var/lib/postgresql\n" in compose
    assert "postgres-data:/var/lib/postgresql/data" not in compose


def test_ci_includes_security_gates() -> None:
    workflow = (_repo_root() / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "gitleaks/gitleaks-action@v2" in workflow
    assert "python3 scripts/audit_dependencies.py" in workflow


@pytest.mark.parametrize("kind", ("file", "directory"))
def test_verification_refuses_existing_isolation_destination(
    tmp_path: Path, kind: str
) -> None:
    destination = tmp_path / "isolation"
    if kind == "file":
        destination.write_bytes(b"retained evidence")
    else:
        destination.mkdir()
    bash = (
        "C:/Program Files/Git/bin/bash.exe"
        if sys.platform == "win32"
        else shutil.which("bash")
    )
    assert bash is not None
    environment = os.environ.copy()
    environment.update(
        {
            "RAGELIT_AUDIT_EXPORT_DIRECTORY": (tmp_path / "audit").as_posix(),
            "RAGELIT_INJECTION_EXPORT_DIRECTORY": (tmp_path / "injection").as_posix(),
            "RAGELIT_ISOLATION_EXPORT_DIRECTORY": destination.as_posix(),
        }
    )
    process = subprocess.run(
        [
            bash,
            "-c",
            "docker() { return 0; }; node() { return 0; }; "
            'uv() { if [[ "$1" == sync ]]; then return 0; fi; return 73; }; '
            'source "$1"',
            "verify-test",
            (_repo_root() / "scripts/verify.sh").as_posix(),
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert process.returncode == 2, process.stdout + process.stderr
    assert process.stderr == "Isolation exports need a fresh destination.\n"
    assert "Backend format" not in process.stdout
    if kind == "file":
        assert destination.read_bytes() == b"retained evidence"
    else:
        assert destination.is_dir() and not tuple(destination.iterdir())


def test_ci_runs_real_vector_store_and_compose_stays_local() -> None:
    workflow = import_module("yaml").safe_load(
        (_repo_root() / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    )
    ci_service = workflow["jobs"]["verify"]["services"]["qdrant"]
    compose_service = _qdrant_compose_service()
    expected_image = (
        "qdrant/qdrant:v1.19.2@sha256:"
        "b7b0444c4c351c970b98e90a6f89c2ee4287c65b44e52b4cb503fa5b2aa927ad"
    )
    assert ci_service["image"] == compose_service["image"] == expected_image
    assert ci_service["ports"] == ["6333:6333"]
    assert {
        (port["host_ip"], port["target"], port["published"])
        for port in compose_service["ports"]
    } == {("127.0.0.1", 6333, "6333"), ("127.0.0.1", 6334, "6334")}
    compose = (_repo_root() / "compose.yml").read_text(encoding="utf-8")
    assert '"127.0.0.1:5432:5432"' in compose


def test_qdrant_client_matches_configured_server() -> None:
    server_image = _qdrant_compose_service()["image"]
    server_version = server_image.split(":v", 1)[1].split("@", 1)[0]
    client_version = metadata.version("qdrant-client")
    assert client_version.split(".")[:2] == server_version.split(".")[:2]


def test_verification_runs_all_browser_journeys() -> None:
    for name in ("verify.ps1", "verify.sh"):
        script = (_repo_root() / "scripts" / name).read_text(encoding="utf-8")
        assert "Browser journeys" in script
        assert "auth.spec.ts" not in script
        assert "tenant-navigation.spec.ts" not in script


def test_browser_journeys_use_the_production_bundle() -> None:
    config = (_repo_root() / "frontend" / "playwright.config.ts").read_text(
        encoding="utf-8"
    )
    assert "npm run build" in config
    assert "npm run preview" in config
    assert "npm run dev" not in config
    package = json.loads(
        (_repo_root() / "frontend" / "package.json").read_text(encoding="utf-8")
    )
    assert package["scripts"]["preview"] == "vite preview"
