import subprocess
import sys
from collections.abc import Callable
from typing import Any, cast

import pytest

from tests.integration import test_postgres_image_candidate as postgres_candidate
from tests.integration import test_qdrant_image_candidate as qdrant_candidate


@pytest.fixture(
    params=[postgres_candidate._docker, qdrant_candidate._docker],
    ids=["postgres", "qdrant"],
)
def docker(request: pytest.FixtureRequest) -> Callable[..., str]:
    return cast(Callable[..., str], request.param)


def _docker_child(monkeypatch: pytest.MonkeyPatch, program: str) -> None:
    original = subprocess.Popen

    def launch(command: list[str], **kwargs: Any) -> Any:
        assert command in (
            ["docker", "create", "synthetic-image"],
            ["docker", "logs", "synthetic-image"],
        )
        return original([sys.executable, "-c", program], **kwargs)

    monkeypatch.setattr(subprocess, "Popen", launch)


def test_docker_output_excludes_pull_progress(
    docker: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _docker_child(
        monkeypatch,
        "import sys; "
        "sys.stderr.write('Unable to find image locally\\nPulling from synthetic\\n'); "
        "print('synthetic-container-id')",
    )

    assert docker("create", "synthetic-image") == "synthetic-container-id"


def test_docker_failure_preserves_stderr(
    docker: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _docker_child(
        monkeypatch,
        "import sys; sys.stderr.write('synthetic Docker failure\\n'); sys.exit(7)",
    )

    with pytest.raises(subprocess.CalledProcessError) as error:
        docker("create", "synthetic-image")

    assert error.value.returncode == 7
    assert error.value.stderr == "synthetic Docker failure\n"


def test_docker_logs_include_container_stderr(
    docker: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _docker_child(
        monkeypatch,
        "import sys; print('synthetic startup'); "
        "sys.stderr.write('synthetic server error\\n')",
    )

    output = docker("logs", "synthetic-image", include_stderr=True)

    assert set(output.splitlines()) == {"synthetic startup", "synthetic server error"}
