from pathlib import Path
from typing import cast

import pytest

from app.audits.fixtures import generate_fixtures
from app.audits.workspace import AuditWorkspace, AuditWorkspaceError
from tests.unit.audits.test_workspace import configuration


def test_post_filter_requires_opt_in_before_using_any_workspace() -> None:
    from app.audits.isolation_lab import LabPostFilterStore

    with pytest.raises(AuditWorkspaceError, match="audit_lab_opt_in_required"):
        LabPostFilterStore(cast(AuditWorkspace, None), lab=False)


@pytest.mark.parametrize("unsafe", ["closed", "production", "tenant"])
def test_post_filter_refuses_non_lab_workspace_before_accessing_transport(
    tmp_path: Path, unsafe: str
) -> None:
    from app.audits.isolation_lab import LabPostFilterStore

    config = configuration(tmp_path)
    workspace = AuditWorkspace(config, generate_fixtures())
    expected = "audit_workspace_not_open"
    if unsafe == "production":
        config = config.model_copy(update={"environment": "production"})
        expected = "invalid_audit_strategy"
    elif unsafe == "tenant":
        config = configuration(tmp_path, vector_strategy="tenant_collections")
        expected = "invalid_audit_strategy"
    workspace.config = config
    with pytest.raises(AuditWorkspaceError, match=expected):
        LabPostFilterStore(workspace, lab=True)
    assert workspace._vector_client is None
    assert not config.root.exists()
