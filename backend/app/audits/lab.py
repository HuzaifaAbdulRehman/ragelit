from typing import Any

from qdrant_client import models

from app.audits.workspace import AuditWorkspace, AuditWorkspaceError


class LabQueryClient:
    def __init__(self, workspace: AuditWorkspace, profile: str, *, lab: bool) -> None:
        if not lab:
            raise AuditWorkspaceError("audit_lab_opt_in_required")
        if profile not in {"vulnerable", "deny_all"}:
            raise AuditWorkspaceError("invalid_audit_profile")
        workspace.validate_owned()
        self.workspace, self.profile = workspace, profile

    def query_points(self, *args: Any, **kwargs: Any) -> models.QueryResponse:
        collection = args[0] if args else kwargs.get("collection_name")
        if collection != self.workspace.config.name or (
            "collection_name" in kwargs
            and kwargs["collection_name"] != self.workspace.config.name
        ):
            raise AuditWorkspaceError("audit_lab_target_mismatch")
        self.workspace.validate_owned()
        if self.profile == "vulnerable":
            kwargs["query_filter"] = None
            kwargs["prefetch"] = [
                prefetch.model_copy(update={"filter": None})
                for prefetch in kwargs["prefetch"]
            ]
        response = self.workspace.store.client.query_points(*args, **kwargs)
        if self.profile == "deny_all":
            return response.model_copy(update={"points": []})
        return response
