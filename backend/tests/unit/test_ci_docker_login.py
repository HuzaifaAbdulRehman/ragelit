import os
import shutil
import subprocess
import sys
from importlib import import_module
from pathlib import Path
from typing import Any, cast

import pytest


def login_step() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[3]
    workflow = import_module("yaml").safe_load(
        (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["verify"]["steps"]
    matches = [step for step in steps if step.get("id") == "dockerhub"]
    assert len(matches) == 1, "CI needs a Docker Hub authentication step"
    step = matches[0]
    build_index = next(
        index
        for index, item in enumerate(steps)
        if item.get("name") == "Build patched service images"
    )
    assert steps.index(step) < build_index
    assert step["env"]["DOCKERHUB_USERNAME"] == "${{ secrets.DOCKERHUB_USERNAME }}"
    assert step["env"]["DOCKERHUB_TOKEN"] == "${{ secrets.DOCKERHUB_TOKEN }}"
    assert "DOCKERHUB_TOKEN" not in workflow["jobs"]["verify"]["env"]
    assert "continue-on-error" not in step
    logout = next(item for item in steps if item.get("id") == "dockerhub_logout")
    assert steps.index(logout) > build_index
    setup_index = next(
        index
        for index, item in enumerate(steps)
        if item.get("uses") == "astral-sh/setup-uv@v6"
    )
    assert steps.index(logout) < setup_index
    assert logout["if"] == (
        "${{ always() && steps.dockerhub.outputs.authenticated == 'true' }}"
    )
    assert logout["run"] == "docker logout"
    return cast(dict[str, Any], step)


@pytest.mark.parametrize(
    (
        "event",
        "actor",
        "head",
        "username",
        "token",
        "login_exit",
        "expected_exit",
        "authenticated",
    ),
    (
        ("push", "owner", "", "fixture-user", "fixture-token", 0, 0, True),
        (
            "pull_request",
            "owner",
            "owner/ragelit",
            "fixture-user",
            "fixture-token",
            0,
            0,
            True,
        ),
        ("push", "owner", "", "", "", 0, 0, False),
        ("push", "owner", "", "fixture-user", "", 0, 1, False),
        ("push", "owner", "", "", "fixture-token", 0, 1, False),
        ("push", "owner", "", "fixture-user", "fixture-token", 37, 37, False),
        (
            "pull_request",
            "outsider",
            "outsider/ragelit",
            "fixture-user",
            "fixture-token",
            0,
            0,
            False,
        ),
        (
            "pull_request",
            "dependabot[bot]",
            "owner/ragelit",
            "fixture-user",
            "fixture-token",
            0,
            0,
            False,
        ),
        ("push", "dependabot[bot]", "", "fixture-user", "fixture-token", 0, 0, False),
        (
            "pull_request_target",
            "owner",
            "owner/ragelit",
            "fixture-user",
            "fixture-token",
            0,
            0,
            False,
        ),
    ),
)
def test_ci_authentication_respects_event_and_secret_boundaries(
    tmp_path: Path,
    event: str,
    actor: str,
    head: str,
    username: str,
    token: str,
    login_exit: int,
    expected_exit: int,
    authenticated: bool,
) -> None:
    step = login_step()
    bash = (
        "C:/Program Files/Git/bin/bash.exe"
        if sys.platform == "win32"
        else shutil.which("bash")
    )
    assert bash is not None
    output_path = tmp_path / "outputs"
    environment = os.environ | {
        "GITHUB_EVENT_NAME": event,
        "GITHUB_ACTOR": actor,
        "GITHUB_REPOSITORY": "owner/ragelit",
        "PR_HEAD_REPOSITORY": head,
        "DOCKERHUB_USERNAME": username,
        "DOCKERHUB_TOKEN": token,
        "GITHUB_OUTPUT": output_path.as_posix(),
        "DOCKER_LOGIN_EXIT": str(login_exit),
    }
    docker_boundary = """
docker() {
  printf 'argument=<%s>\\n' "$@"
  [[ "$#" == 4 && "$1" == login && "$2" == --username &&
     "$3" == fixture-user && "$4" == --password-stdin ]] || return 92
  local supplied
  supplied="$(cat)"
  [[ "$supplied" == fixture-token ]] || return 93
  return "$DOCKER_LOGIN_EXIT"
}
"""
    result = subprocess.run(
        [bash, "-e", "-o", "pipefail", "-c", docker_boundary + step["run"]],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == expected_exit, result.stdout + result.stderr
    assert "fixture-token" not in result.stdout + result.stderr
    output = output_path.read_text() if output_path.exists() else ""
    assert output == ("authenticated=true\n" if authenticated else "")
    attempted = authenticated or login_exit != 0
    if attempted:
        assert result.stdout == (
            "argument=<login>\nargument=<--username>\n"
            "argument=<fixture-user>\nargument=<--password-stdin>\n"
        )
    else:
        assert "argument=<" not in result.stdout
