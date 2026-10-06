import json
from pathlib import Path
from typing import Literal

import pytest

from app.evaluation.access_reports import write_access_benchmark
from app.evaluation.security_reports import write_injection_benchmark
from tests.unit.evaluation.test_access_reports import benchmark
from tests.unit.evaluation.test_cli import unreachable
from tests.unit.evaluation.test_security_comparisons import injection

Kind = Literal["access", "injection"]


def source_paths(root: Path, kind: Kind, failure: str = "") -> tuple[Path, Path]:
    if kind == "access":
        reference, candidate = (
            benchmark(),
            benchmark(lab=True, provisional=failure == "partial"),
        )
        if failure == "source_drift":
            candidate = candidate.model_copy(
                update={
                    "provenance": candidate.provenance.model_copy(
                        update={"git_revision": "b" * 40}
                    )
                }
            )
        paths = (
            write_access_benchmark(reference, root / "reference"),
            write_access_benchmark(candidate, root / "candidate"),
        )
    else:
        first, second = (
            injection(),
            injection(leak=True, provisional=failure == "partial"),
        )
        if failure == "source_drift":
            second = second.model_copy(
                update={
                    "provenance": second.provenance.model_copy(
                        update={"git_revision": "b" * 40}
                    )
                }
            )
        paths = (
            write_injection_benchmark(first, root / "reference"),
            write_injection_benchmark(second, root / "candidate"),
        )
    return (
        paths
        if failure != "missing"
        else (paths[0], root / "ExceptionSecretMarker.json")
    )


@pytest.mark.parametrize("kind", ["access", "injection"])
def test_security_cli_pairs_original_reports_without_loading_runtime(
    kind: Kind,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.evaluation import cli, models

    for name in ("_configuration", "_run", "_run_access", "_run_injection"):
        monkeypatch.setattr(cli, name, unreachable)
    monkeypatch.setattr(models, "PinnedEmbeddingProvider", unreachable)
    paths = source_paths(tmp_path, kind)
    original = tuple(path.read_bytes() for path in paths)
    assert (
        cli.main([f"--compare-{kind}-benchmarks", *(str(path) for path in paths)]) == 0
    )
    output = capsys.readouterr()
    assert output.err == ""
    result = json.loads(output.out)
    assert result["code"] == "security_comparison_complete"
    assert result["exit_code"] == 0
    comparison = result["comparison"]
    assert comparison["reference_gate"] == 0
    assert comparison["candidate_gate"] == 1
    assert len(comparison["exposures"]) == 5
    assert comparison["exposures"][0]["interval"]["paired_queries"] == (
        51 if kind == "access" else 6
    )
    if kind == "access":
        assert comparison["exposures"][0]["interval"][
            "mean_difference"
        ] == pytest.approx(1 / 51)
        assert comparison["injection_asr"] is None
    else:
        assert comparison["injection_asr"]["mean_difference"] == 1.0
        assert comparison["injection_asr"]["paired_queries"] == 3
    assert tuple(path.read_bytes() for path in paths) == original


@pytest.mark.parametrize("kind", ["access", "injection"])
@pytest.mark.parametrize("failure", ["missing", "partial", "source_drift"])
def test_invalid_security_cli_comparison_is_redacted(
    kind: Kind,
    failure: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.evaluation import cli

    monkeypatch.setattr(cli, "_configuration", unreachable)
    paths = source_paths(tmp_path, kind, failure)
    assert (
        cli.main([f"--compare-{kind}-benchmarks", *(str(path) for path in paths)]) == 2
    )
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {
        "code": "utility_artifact_validation_failed",
        "exit_code": 2,
    }
    assert "ExceptionSecretMarker" not in output.out


@pytest.mark.parametrize("kind", ["access", "injection"])
@pytest.mark.parametrize(
    "extra",
    [
        ["--provider", "fixture"],
        ["--validate-report", "missing"],
        ["--compare-reports", "one", "two"],
    ],
)
def test_security_comparison_modes_reject_runtime_or_other_offline_flags(
    kind: Kind,
    extra: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.evaluation import cli

    monkeypatch.setattr(cli, "_configuration", unreachable)
    assert cli.main([f"--compare-{kind}-benchmarks", "one", "two", *extra]) == 2
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {
        "code": "utility_invalid_arguments",
        "exit_code": 2,
    }
