import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.audits.lab import LabQueryClient
from app.audits.workspace import AuditWorkspace, AuditWorkspaceError


def test_lab_client_refuses_construction_without_opt_in() -> None:
    with pytest.raises(AuditWorkspaceError, match="audit_lab_opt_in_required"):
        LabQueryClient(None, "vulnerable", lab=False)  # type: ignore[arg-type]


def test_lab_module_is_absent_from_the_normal_web_dependency_graph() -> None:
    app_root = Path(__file__).resolve().parents[3] / "app"
    for source in app_root.rglob("*.py"):
        if source.is_relative_to(app_root / "audits"):
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("app.audits"), source
            elif isinstance(node, ast.Import):
                assert all(
                    not alias.name.startswith("app.audits") for alias in node.names
                ), source


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
