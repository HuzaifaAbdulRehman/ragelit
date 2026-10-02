import argparse
import json
import os
import subprocess
import sys
import tomllib
import zipfile
from collections.abc import Sequence
from pathlib import Path


def validate_lockfiles(python_lock: Path, npm_lock: Path) -> None:
    with python_lock.open("rb") as source:
        packages = tomllib.load(source).get("package")
    if not isinstance(packages, list) or not packages:
        raise ValueError("Python lock must contain resolved packages")
    for package in packages:
        if not isinstance(package, dict) or not all(
            isinstance(package.get(field), str) and package[field]
            for field in ("name", "version")
        ):
            raise ValueError("Python lock contains an unversioned package")
    packages = json.loads(npm_lock.read_text(encoding="utf-8")).get("packages")
    if not isinstance(packages, dict) or not any(packages):
        raise ValueError("npm lock must contain resolved packages")
    dependencies = {name: package for name, package in packages.items() if name}
    if not dependencies:
        raise ValueError("npm lock contains no dependencies")
    for name, package in dependencies.items():
        if (
            not name.startswith("node_modules/")
            or not isinstance(package, dict)
            or not isinstance(package.get("version"), str)
            or not package["version"]
        ):
            raise ValueError("npm lock contains an unversioned dependency")


def validate_database(database: Path) -> None:
    with zipfile.ZipFile(database) as archive:
        entries = [
            entry for entry in archive.infolist() if entry.filename.endswith(".json")
        ]
        if not entries:
            raise ValueError(f"No advisory records in {database}")
        for entry in entries:
            advisory = json.loads(archive.read(entry))
            if (
                not isinstance(advisory, dict)
                or not all(
                    isinstance(advisory.get(field), str) and advisory[field]
                    for field in ("id", "modified")
                )
                or not isinstance(advisory.get("affected", []), list)
            ):
                raise ValueError(f"Invalid advisory record: {entry.filename}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit locked dependencies offline")
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args(argv)
    database = os.environ.get("OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY")
    if not database:
        print(
            "Set OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY before auditing", file=sys.stderr
        )
        return 2
    repo_root = args.repo_root.resolve()
    lockfiles = [
        repo_root / "backend/uv.lock",
        repo_root / "frontend/package-lock.json",
    ]
    inputs = lockfiles + [
        Path(database) / "osv-scalibr" / ecosystem / "all.zip"
        for ecosystem in ("PyPI", "npm")
    ]
    for target in inputs:
        if not target.is_file() or target.stat().st_size == 0:
            print(f"Missing or empty audit input: {target}", file=sys.stderr)
            return 2
    try:
        validate_lockfiles(*lockfiles)
        for target in inputs[2:]:
            validate_database(target)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(f"Invalid audit input: {error}", file=sys.stderr)
        return 2
    command = [
        os.environ.get("OSV_SCANNER", "osv-scanner"),
        "scan",
        "source",
        "--offline",
        "--local-db-path",
        database,
    ]
    for lockfile in lockfiles:
        command.extend(["--lockfile", str(lockfile)])
    try:
        return subprocess.run(command, cwd=repo_root, check=False).returncode
    except OSError as error:
        print(f"Cannot run dependency scanner: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
