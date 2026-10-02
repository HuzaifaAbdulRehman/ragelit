import json
import subprocess
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


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
    assert "actions/dependency-review-action@v4" in workflow


def test_ci_runs_real_vector_store_and_compose_stays_local() -> None:
    workflow = (_repo_root() / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    compose = (_repo_root() / "compose.yml").read_text(encoding="utf-8")
    assert "qdrant/qdrant:v1.15.4" in workflow
    assert "6333:6333" in workflow
    for port in (5432, 6333, 6334):
        assert f'"127.0.0.1:{port}:{port}"' in compose


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
