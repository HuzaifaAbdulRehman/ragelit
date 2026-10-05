import ast
import subprocess
import sys
from importlib.util import resolve_name
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.audits.lab import LabQueryClient
from app.audits.workspace import AuditWorkspace, AuditWorkspaceError


def web_modules(root: Path, roots: tuple[str, ...]) -> set[str]:
    pending = list(roots)
    visited: set[str] = set()
    while pending:
        name = pending.pop()
        if name in visited or not (name == "app" or name.startswith("app.")):
            continue
        path = root.joinpath(*name.split(".")[1:])
        source = path / "__init__.py" if path.is_dir() else path.with_suffix(".py")
        if not source.is_file():
            continue
        visited.add(name)
        parts = name.split(".")
        pending.extend(".".join(parts[:i]) for i in range(1, len(parts)))
        package = name if source.name == "__init__.py" else name.rpartition(".")[0]
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                pending.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                target = resolve_name("." * node.level + (node.module or ""), package)
                pending.append(target)
                pending.extend(f"{target}.{alias.name}" for alias in node.names)
    return visited


def test_lab_client_refuses_construction_without_opt_in() -> None:
    with pytest.raises(AuditWorkspaceError, match="audit_lab_opt_in_required"):
        LabQueryClient(None, "vulnerable", lab=False)  # type: ignore[arg-type]


def test_lab_module_is_absent_from_the_normal_web_dependency_graph() -> None:
    app_root = Path(__file__).resolve().parents[3] / "app"
    modules = web_modules(
        app_root, ("app.main", "app.api.router", "app.export_openapi")
    )
    assert {name for name in modules if name.startswith("app.audits.")} <= {
        "app.audits.reports",
        "app.audits.contracts",
        "app.audits.scoring",
        "app.audits.html",
    }
    assert "app.workers.audit" not in modules
    assert "app.audit_jobs.execution" not in modules


@pytest.mark.parametrize(
    "edge",
    ["import app.audits.lab", "from .audits import lab", "from app.bridge import Lab"],
)
def test_web_graph_traces_direct_relative_and_reexport_edges(
    tmp_path: Path, edge: str
) -> None:
    root = tmp_path / "app"
    (root / "audits").mkdir(parents=True)
    (root / "__init__.py").write_text("")
    (root / "main.py").write_text(edge)
    (root / "bridge.py").write_text("from .audits.lab import Lab")
    (root / "audits" / "__init__.py").write_text("")
    (root / "audits" / "lab.py").write_text("class Lab: pass")
    assert "app.audits.lab" in web_modules(root, ("app.main",))


def test_fresh_web_import_does_not_load_privileged_engine() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import app.main, app.export_openapi, sys; "
            "allowed = {'app.audits.reports', 'app.audits.contracts', "
            "'app.audits.scoring', 'app.audits.html'}; "
            "assert not {m for m in sys.modules "
            "if m.startswith('app.audits.')} - allowed; "
            "assert 'app.audit_jobs.execution' not in sys.modules",
        ],
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def test_lab_query_refuses_another_collection_before_calling_transport() -> None:
    class UnreachableClient:
        def query_points(self, *args: Any, **kwargs: Any) -> None:
            raise AssertionError("foreign collection reached transport")

    workspace = SimpleNamespace(
        config=SimpleNamespace(name="ragelit_audit_owned"),
        validate_owned=lambda: None,
        store=SimpleNamespace(client=UnreachableClient()),
    )
    client = LabQueryClient(cast(AuditWorkspace, workspace), "vulnerable", lab=True)
    with pytest.raises(AuditWorkspaceError, match="audit_lab_target_mismatch"):
        client.query_points("different_collection", prefetch=[])
