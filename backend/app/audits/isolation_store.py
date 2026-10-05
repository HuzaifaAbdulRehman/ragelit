from collections.abc import Callable
from uuid import UUID

from qdrant_client import QdrantClient, models

from app.audits.workspace import AuditConfiguration, AuditWorkspaceError
from app.documents.chunking import TextChunk
from app.retrieval.embeddings import Embedding, EmbeddingProvider
from app.retrieval.store import IndexContext, QdrantChunkStore, SearchProjection


class TenantCollectionStore(QdrantChunkStore):
    def __init__(
        self,
        client: QdrantClient,
        config: AuditConfiguration,
        *,
        dimension: int,
        organizations: Callable[[], tuple[UUID, ...]],
    ) -> None:
        if (
            config.environment not in {"local", "test"}
            or config.vector_strategy != "tenant_collections"
        ):
            raise AuditWorkspaceError("invalid_audit_strategy")
        config = AuditConfiguration.model_validate(config.model_dump())
        super().__init__(client, config.name, dimension=dimension)
        self._organizations = organizations
        self._registered()

    def _registered(self) -> tuple[UUID, ...]:
        registered = self._organizations()
        if any(not isinstance(value, UUID) for value in registered) or len(
            set(registered)
        ) != len(registered):
            raise AuditWorkspaceError("invalid_audit_registry")
        return registered

    def _name(self, organization_id: UUID) -> str:
        return f"{self.collection_name}_tenant_{organization_id.hex}"

    def _delegate(self, organization_id: UUID) -> QdrantChunkStore:
        if organization_id not in self._registered():
            raise AuditWorkspaceError("audit_unknown_organization")
        return QdrantChunkStore(
            self.client, self._name(organization_id), dimension=self.dimension
        )

    def collection_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._name(value) for value in self._registered()))

    def _namespace_names(self) -> set[str]:
        return {
            item.name
            for item in self.client.get_collections().collections
            if item.name == self.collection_name
            or item.name.startswith(f"{self.collection_name}_tenant_")
        }

    def ensure_collection(self) -> None:
        if self._namespace_names() != set(self.collection_names()):
            raise AuditWorkspaceError("audit_resource_drift")

    def ensure_tenants(self, organizations: tuple[UUID, ...]) -> None:
        registered = self._registered()
        if len(set(organizations)) != len(organizations) or set(organizations) != set(
            registered
        ):
            raise AuditWorkspaceError("invalid_audit_registry")
        if self._namespace_names():
            raise AuditWorkspaceError("unowned_audit_collection")
        for organization_id in sorted(registered, key=lambda value: value.hex):
            delegate = self._delegate(organization_id)
            delegate._create_collection()
            delegate.ensure_collection()

    def stage(
        self,
        context: IndexContext,
        chunks: tuple[TextChunk, ...],
        provider: EmbeddingProvider,
    ) -> None:
        self._delegate(context.organization_id).stage(context, chunks, provider)

    def activate(self, context: IndexContext) -> None:
        self._delegate(context.organization_id).activate(context)

    def set_access(
        self,
        organization_id: UUID,
        document_id: UUID,
        visibility: str,
        users: tuple[UUID, ...],
        groups: tuple[UUID, ...],
    ) -> None:
        self._delegate(organization_id).set_access(
            organization_id, document_id, visibility, users, groups
        )

    def deactivate(self, organization_id: UUID, document_id: UUID) -> None:
        self._delegate(organization_id).deactivate(organization_id, document_id)

    def search_points(
        self,
        organization_id: UUID,
        versions: tuple[UUID, ...],
        vector: Embedding,
        filters: models.Filter,
        limit: int,
    ) -> SearchProjection:
        return self._delegate(organization_id).search_points(
            organization_id, versions, vector, filters, limit
        )
