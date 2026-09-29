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
    notice = (_repo_root() / "THIRD_PARTY_NOTICES.md").read_text(
        encoding="utf-8"
    )
    assert "cb740b656d7a0a6c5e12c7bf8e50343ec94ee9c7" in notice
    assert "MIT" in notice
