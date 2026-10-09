import json
import os
import subprocess
import time
from collections.abc import Iterator
from http.client import RemoteDisconnected
from unittest.mock import MagicMock
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4

import pytest
from qdrant_client import QdrantClient, models

BASE_IMAGE = (
    "qdrant/qdrant:v1.19.2@sha256:"
    "b7b0444c4c351c970b98e90a6f89c2ee4287c65b44e52b4cb503fa5b2aa927ad"
)
LEASE_LABEL = "ragelit.qdrant-candidate-lease"


def _docker(*args: str, include_stderr: bool = False) -> str:
    completed = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=60, check=True
    )
    return (completed.stdout + (completed.stderr if include_stderr else "")).strip()


def _remove_owned(container: str, lease: str) -> None:
    owner = _docker(
        "inspect",
        "--format",
        '{{index .Config.Labels "' + LEASE_LABEL + '"}}',
        container,
    )
    assert owner == lease, "refuse to remove a container with a different lease"
    _docker("rm", "--force", "--volumes", container)


@pytest.fixture(scope="module")
def candidate_image() -> str:
    image = os.environ.get("RAGELIT_QDRANT_CANDIDATE_IMAGE")
    if not image:
        pytest.skip("set RAGELIT_QDRANT_CANDIDATE_IMAGE for disposable Docker checks")
    return _docker("image", "inspect", "--format", "{{.Id}}", image)


def _metadata(image: str, command: str) -> str:
    lease = uuid4().hex
    container = _docker(
        "create",
        "--name",
        f"ragelit-qdrant-metadata-{lease}",
        "--label",
        f"{LEASE_LABEL}={lease}",
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
        "/bin/sh",
        image,
        "-c",
        command,
    )
    try:
        return _docker("start", "--attach", container)
    finally:
        _remove_owned(container, lease)


def test_candidate_changes_only_fixed_pcre2(candidate_image: str) -> None:
    command = "dpkg-query -W -f='${Package}=${Version}\\n'"
    original = set(_metadata(BASE_IMAGE, command).splitlines())
    candidate = set(_metadata(candidate_image, command).splitlines())
    assert original - candidate == {"libpcre2-8-0=10.46-1~deb13u2"}
    assert candidate - original == {"libpcre2-8-0=10.46-1~deb13u3"}


def test_candidate_preserves_official_startup(candidate_image: str) -> None:
    original = json.loads(_docker("image", "inspect", BASE_IMAGE))[0]["Config"]
    candidate = json.loads(_docker("image", "inspect", candidate_image))[0]["Config"]
    for key in (
        "Entrypoint",
        "Cmd",
        "Env",
        "User",
        "Volumes",
        "ExposedPorts",
        "WorkingDir",
    ):
        assert candidate.get(key) == original.get(key), key


def test_candidate_preserves_binary_and_ui(candidate_image: str) -> None:
    command = (
        "set -e; sha256sum /qdrant/qdrant /qdrant/entrypoint.sh; "
        "find /qdrant/static -type f -exec sha256sum {} + | LC_ALL=C sort"
    )
    original = _metadata(BASE_IMAGE, command)
    assert "/qdrant/static/" in original
    assert _metadata(candidate_image, command) == original


def _wait_ready(url: str) -> bool:
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            with opener.open(f"{url}/readyz", timeout=2) as response:
                if response.status == 200:
                    return True
        except (URLError, RemoteDisconnected):
            pass
        time.sleep(0.25)
    return False


def test_readiness_retries_early_disconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    response = MagicMock()
    response.__enter__.return_value.status = 200
    opener = MagicMock()
    opener.open.side_effect = [RemoteDisconnected("startup"), response]
    monkeypatch.setitem(_wait_ready.__globals__, "build_opener", lambda *args: opener)
    assert _wait_ready("http://127.0.0.1:6333")
    assert opener.open.call_count == 2


@pytest.fixture(scope="module")
def candidate_client(candidate_image: str) -> Iterator[QdrantClient]:
    lease = uuid4().hex
    container = _docker(
        "create",
        "--name",
        f"ragelit-qdrant-candidate-{lease}",
        "--label",
        f"{LEASE_LABEL}={lease}",
        "--memory",
        "512m",
        "--cpus",
        "0.5",
        "--mount",
        "type=volume,destination=/qdrant/storage",
        "--publish",
        "127.0.0.1::6333",
        candidate_image,
    )
    try:
        _docker("start", container)
        state = json.loads(_docker("inspect", container))[0]
        port = state["NetworkSettings"]["Ports"]["6333/tcp"][0]["HostPort"]
        url = f"http://127.0.0.1:{port}"
        if not _wait_ready(url):
            logs = _docker("logs", container, include_stderr=True)
            pytest.fail(f"candidate startup timed out: {logs}")
        client = QdrantClient(url=url, timeout=10, trust_env=False)
        try:
            yield client
        finally:
            client.close()
    finally:
        _remove_owned(container, lease)


def test_candidate_reads_and_writes_vectors(candidate_client: QdrantClient) -> None:
    client = candidate_client
    collection = "synthetic_candidate"
    client.create_collection(
        collection,
        vectors_config=models.VectorParams(size=3, distance=models.Distance.COSINE),
    )
    client.upsert(
        collection,
        points=[
            models.PointStruct(
                id=1, vector=[1.0, 0.0, 0.0], payload={"kind": "synthetic"}
            ),
            models.PointStruct(
                id=2, vector=[0.0, 1.0, 0.0], payload={"kind": "synthetic"}
            ),
        ],
        wait=True,
    )
    points = client.retrieve(collection, ids=[1], with_vectors=True)
    assert len(points) == 1
    assert points[0].payload == {"kind": "synthetic"}
    assert points[0].vector == [1.0, 0.0, 0.0]
    result = client.query_points(collection, query=[1.0, 0.0, 0.0], limit=1).points
    assert [point.id for point in result] == [1]
    assert result[0].score == pytest.approx(1.0)
    client.delete(
        collection, points_selector=models.PointIdsList(points=[1, 2]), wait=True
    )
    assert client.count(collection, exact=True).count == 0
