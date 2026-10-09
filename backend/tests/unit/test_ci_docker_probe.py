import os
import shutil
import subprocess
import sys
from importlib import import_module
from pathlib import Path

import pytest


@pytest.mark.parametrize("curl_exit", (0, 6, 28))
def test_public_docker_probe_reports_failures_without_using_credentials(
    tmp_path: Path, curl_exit: int
) -> None:
    root = Path(__file__).resolve().parents[3]
    workflow = import_module("yaml").safe_load(
        (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["verify"]["steps"]
    probes = [step for step in steps if step.get("id") == "dockerhub_probe"]
    assert len(probes) == 1, "CI needs a credential-free Docker endpoint probe"
    probe = probes[0]
    login = next(step for step in steps if step.get("id") == "dockerhub")
    assert steps.index(probe) < steps.index(login)
    assert not probe.get("env")
    assert probe["timeout-minutes"] == 1
    bash = (
        "C:/Program Files/Git/bin/bash.exe"
        if sys.platform == "win32"
        else shutil.which("bash")
    )
    assert bash is not None
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"DOCKERHUB_USERNAME", "DOCKERHUB_TOKEN"}
    }
    environment["CURL_TEST_EXIT"] = str(curl_exit)
    curl_boundary = """
probe_call=0
curl() {
  ((probe_call+=1))
  local endpoint="${!#}"
  [[ "$endpoint" == https://auth.docker.io/ ||
     "$endpoint" == https://registry-1.docker.io/v2/ ]] || return 94
  local format='HTTP %{http_code}, DNS %{time_namelookup}s, '
  format+='connect %{time_connect}s, total %{time_total}s\\n'
  local expected=(--disable --silent --show-error --output /dev/null
    --connect-timeout 5 --max-time 10 --write-out "$format")
  case "$probe_call" in
    1|3) : ;;
    2|4) expected+=(--ipv4) ;;
    *) return 95 ;;
  esac
  expected+=("$endpoint")
  [[ "$#" == "${#expected[@]}" ]] || return 92
  local argument
  for argument in "${expected[@]}"; do
    [[ "$1" == "$argument" ]] || return 93
    shift
  done
  printf 'HTTP 000, DNS 0.01s, connect 0.02s, total 0.03s\\n'
  return "$CURL_TEST_EXIT"
}
"""
    result = subprocess.run(
        [bash, "-e", "-o", "pipefail", "-c", curl_boundary + probe["run"]],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    expected = ""
    for endpoint in ("https://auth.docker.io/", "https://registry-1.docker.io/v2/"):
        for mode in ("default", "ipv4"):
            expected += (
                f"{endpoint} {mode}: HTTP 000, DNS 0.01s, connect 0.02s, total 0.03s\n"
            )
            if curl_exit:
                expected += f"probe exit={curl_exit}\n"
    assert result.stdout == expected
