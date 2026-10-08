import json
import os
import subprocess
import time
from collections.abc import Iterator
from uuid import uuid4

import pytest

BASE_IMAGE = (
    "postgres:18.6-alpine3.24@sha256:"
    "77f585114c32fbca283dc835b0596f4e52b51b4c6662d7810b2f4084f60a1873"
)
LEASE_LABEL = "ragelit.postgres-candidate-lease"


def _docker(*args: str) -> str:
    return subprocess.check_output(
        ["docker", *args], text=True, stderr=subprocess.STDOUT, timeout=60
    ).strip()


@pytest.fixture(scope="module")
def candidate_image() -> str:
    image = os.environ.get("RAGELIT_POSTGRES_CANDIDATE_IMAGE")
    if not image:
        pytest.skip("set RAGELIT_POSTGRES_CANDIDATE_IMAGE for disposable Docker checks")
    return _docker("image", "inspect", "--format", "{{.Id}}", image)


def _inventory(image: str) -> set[str]:
    output = _docker(
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--memory",
        "128m",
        "--cpus",
        "0.5",
        "--entrypoint",
        "apk",
        image,
        "--no-network",
        "list",
        "--installed",
    )
    return {line.split()[0] for line in output.splitlines() if "[installed]" in line}


def test_candidate_changes_only_fixed_zlib(candidate_image: str) -> None:
    original = _inventory(BASE_IMAGE)
    candidate = _inventory(candidate_image)
    assert original - candidate == {"zlib-1.3.2-r0"}
    assert candidate - original == {"zlib-1.3.2-r1"}


def test_candidate_preserves_official_startup(candidate_image: str) -> None:
    original = json.loads(_docker("image", "inspect", BASE_IMAGE))[0]["Config"]
    candidate = json.loads(_docker("image", "inspect", candidate_image))[0]["Config"]
    for key in ("Entrypoint", "Cmd", "Env", "User", "Volumes", "ExposedPorts"):
        assert candidate.get(key) == original.get(key), key


@pytest.fixture(scope="module")
def candidate_database(candidate_image: str) -> Iterator[str]:
    lease = uuid4().hex
    name = f"ragelit-pg-candidate-{lease}"
    container = _docker(
        "create",
        "--name",
        name,
        "--label",
        f"{LEASE_LABEL}={lease}",
        "--network",
        "none",
        "--memory",
        "256m",
        "--cpus",
        "0.5",
        "--tmpfs",
        "/var/lib/postgresql:rw,size=134217728",
        "--env",
        "POSTGRES_PASSWORD=synthetic-candidate-password",
        candidate_image,
        "postgres",
        "-c",
        "shared_buffers=16MB",
        "-c",
        "max_connections=20",
        "-c",
        "listen_addresses=",
    )
    try:
        _docker("start", container)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            ready = subprocess.run(
                [
                    "docker",
                    "exec",
                    container,
                    "sh",
                    "-c",
                    'test "$(cat /proc/1/comm)" = postgres && pg_isready -U postgres',
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if ready.returncode == 0:
                yield container
                return
            time.sleep(0.25)
        pytest.fail(f"candidate startup timed out: {_docker('logs', container)}")
    finally:
        owner = _docker(
            "inspect",
            "--format",
            '{{index .Config.Labels "' + LEASE_LABEL + '"}}',
            container,
        )
        assert owner == lease, "refuse to remove a container with a different lease"
        _docker("rm", "--force", "--volumes", container)


def test_candidate_restores_compressed_dump(candidate_database: str) -> None:
    container = candidate_database
    _docker("exec", container, "createdb", "-U", "postgres", "candidate_source")
    _docker(
        "exec",
        container,
        "psql",
        "-U",
        "postgres",
        "-d",
        "candidate_source",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        "CREATE TABLE documents (id integer PRIMARY KEY, body text NOT NULL); "
        "INSERT INTO documents SELECT n, repeat('synthetic document ', 500) "
        "FROM generate_series(1, 100) AS n;",
    )
    _docker(
        "exec",
        container,
        "pg_dump",
        "-U",
        "postgres",
        "-d",
        "candidate_source",
        "--format=custom",
        "--compress=gzip:6",
        "--file=/tmp/candidate.dump",
    )
    _docker("exec", container, "createdb", "-U", "postgres", "candidate_restore")
    _docker(
        "exec",
        container,
        "pg_restore",
        "-U",
        "postgres",
        "-d",
        "candidate_restore",
        "--exit-on-error",
        "/tmp/candidate.dump",
    )
    restored = _docker(
        "exec",
        container,
        "psql",
        "-U",
        "postgres",
        "-d",
        "candidate_restore",
        "-v",
        "ON_ERROR_STOP=1",
        "-Atc",
        "SELECT count(*), sum(length(body)) FROM documents;",
    )
    assert restored == "100|950000"
