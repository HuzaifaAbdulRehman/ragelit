import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


@unittest.skipUnless(
    os.environ.get("OSV_SCANNER"), "Set OSV_SCANNER to test the real CLI"
)
class OfflineAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ragelit-audit-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.environment = {
            **os.environ,
            "OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY": str(self.root / "db"),
        }
        for ecosystem in ("PyPI", "npm"):
            target = self.root / "db/osv-scalibr" / ecosystem / "all.zip"
            target.parent.mkdir(parents=True)
            advisory = {
                "id": f"TEST-{ecosystem}-001",
                "modified": "2026-01-01T00:00:00Z",
                "affected": [
                    {
                        "package": {"name": "audit-fixture", "ecosystem": ecosystem},
                        "versions": ["1.0.0"],
                    }
                ],
            }
            with zipfile.ZipFile(target, "w") as database:
                database.writestr("advisory.json", json.dumps(advisory))
        self.lockfiles("2.0.0", "2.0.0")

    def lockfiles(self, python_version: str, npm_version: str) -> None:
        backend = self.root / "backend"
        frontend = self.root / "frontend"
        backend.mkdir(exist_ok=True)
        frontend.mkdir(exist_ok=True)
        (backend / "uv.lock").write_text(
            'version = 1\nrequires-python = ">=3.12"\n[[package]]\n'
            f'name = "audit-fixture"\nversion = "{python_version}"\n'
            'source = { registry = "https://pypi.org/simple" }\n',
            encoding="utf-8",
        )
        (frontend / "package-lock.json").write_text(
            json.dumps(
                {
                    "name": "audit-test",
                    "lockfileVersion": 3,
                    "packages": {
                        "node_modules/audit-fixture": {"version": npm_version}
                    },
                }
            ),
            encoding="utf-8",
        )

    def scan(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "audit_dependencies.py"),
                "--repo-root",
                str(self.root),
            ],
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def test_clean_lockfiles_pass(self) -> None:
        result = self.scan()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_vulnerable_python_dependency_blocks(self) -> None:
        self.lockfiles("1.0.0", "2.0.0")
        self.assertEqual(self.scan().returncode, 1)

    def test_vulnerable_npm_dependency_blocks(self) -> None:
        self.lockfiles("2.0.0", "1.0.0")
        self.assertEqual(self.scan().returncode, 1)

    def test_malformed_lockfile_blocks(self) -> None:
        (self.root / "backend/uv.lock").write_text("invalid toml [", encoding="utf-8")
        self.assertNotEqual(self.scan().returncode, 0)

    def test_corrupt_database_blocks(self) -> None:
        (self.root / "db/osv-scalibr/npm/all.zip").write_bytes(b"broken zip")
        self.assertNotEqual(self.scan().returncode, 0)

    def test_malformed_advisory_cannot_hide_vulnerable_dependency(self) -> None:
        self.lockfiles("2.0.0", "1.0.0")
        with zipfile.ZipFile(self.root / "db/osv-scalibr/npm/all.zip", "w") as database:
            database.writestr("advisory.json", "{broken json")
        self.assertNotEqual(self.scan().returncode, 0)

    def test_advisory_without_identity_blocks(self) -> None:
        with zipfile.ZipFile(self.root / "db/osv-scalibr/npm/all.zip", "w") as database:
            database.writestr("advisory.json", "{}")
        self.assertNotEqual(self.scan().returncode, 0)

    def test_empty_database_blocks(self) -> None:
        with zipfile.ZipFile(self.root / "db/osv-scalibr/npm/all.zip", "w"):
            pass
        self.assertNotEqual(self.scan().returncode, 0)

    def test_python_lock_without_packages_blocks(self) -> None:
        (self.root / "backend/uv.lock").write_text("version = 1", encoding="utf-8")
        self.assertNotEqual(self.scan().returncode, 0)

    def test_npm_lock_without_packages_blocks(self) -> None:
        (self.root / "frontend/package-lock.json").write_text("{}", encoding="utf-8")
        self.assertNotEqual(self.scan().returncode, 0)


if __name__ == "__main__":
    unittest.main()
