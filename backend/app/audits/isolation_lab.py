from typing import Any, cast
from uuid import UUID

from qdrant_client import models

from app.audits.lab import LabQueryClient
from app.audits.workspace import AuditWorkspace, AuditWorkspaceError
from app.retrieval.embeddings import Embedding
from app.retrieval.store import QdrantChunkStore, SearchProjection


class LabPostFilterStore(QdrantChunkStore):
    def __init__(self, workspace: AuditWorkspace, *, lab: bool) -> None:
        if not lab:
            raise AuditWorkspaceError("audit_lab_opt_in_required")
        if (
            workspace.config.environment not in {"local", "test"}
            or workspace.config.vector_strategy != "shared_pre_filter"
        ):
            raise AuditWorkspaceError("invalid_audit_strategy")
        workspace.validate_owned()
        self.workspace = workspace
        super().__init__(
            workspace.store.client,
            workspace.store.collection_name,
            dimension=workspace.store.dimension,
        )
        if self.collection_names() != (workspace.config.name,):
            raise AuditWorkspaceError("audit_lab_target_mismatch")
        self._unfiltered = QdrantChunkStore(
            cast(Any, LabQueryClient(workspace, "vulnerable", lab=True)),
            self.collection_name,
            dimension=self.dimension,
        )

    def search_points(
        self,
        organization_id: UUID,
        versions: tuple[UUID, ...],
        vector: Embedding,
        filters: models.Filter,
        limit: int,
    ) -> SearchProjection:
        workspace = self.workspace
        if (
            workspace.config.environment not in {"local", "test"}
            or workspace.config.vector_strategy != "shared_pre_filter"
        ):
            raise AuditWorkspaceError("invalid_audit_strategy")
        if (
            self.client is not workspace.store.client
            or self.collection_names() != workspace.store.collection_names()
            or self.collection_name != workspace.config.name
            or self._unfiltered.collection_name != workspace.config.name
        ):
            raise AuditWorkspaceError("audit_lab_target_mismatch")
        raw = self._unfiltered.search_points(
            organization_id, versions, vector, filters, limit
        ).raw
        permitted_versions = {str(value) for value in versions}
        accepted = tuple(
            point
            for point in raw
            if point.payload is not None
            and point.payload.get("organization_id") == str(organization_id)
            and point.payload.get("active") is True
            and isinstance(point.payload.get("document_version_id"), str)
            and point.payload.get("document_version_id") in permitted_versions
        )
        return SearchProjection(raw, accepted)
