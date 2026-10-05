from dataclasses import dataclass
from uuid import UUID, uuid5

from qdrant_client import QdrantClient, models

from app.documents.chunking import TextChunk
from app.documents.extraction import DocumentError
from app.retrieval.embeddings import Embedding, EmbeddingProvider


@dataclass(frozen=True, slots=True)
class SearchProjection:
    raw: tuple[models.ScoredPoint, ...]
    accepted: tuple[models.ScoredPoint, ...]


@dataclass(frozen=True, slots=True)
class IndexContext:
    organization_id: UUID
    document_id: UUID
    version_id: UUID
    claim_id: UUID
    filename: str
    visibility: str
    user_ids: tuple[UUID, ...]
    group_ids: tuple[UUID, ...]


def field_match(key: str, value: str | bool) -> models.FieldCondition:
    return models.FieldCondition(key=key, match=models.MatchValue(value=value))


class QdrantChunkStore:
    def __init__(
        self, client: QdrantClient, collection_name: str, *, dimension: int
    ) -> None:
        self.client = client
        self.collection_name = collection_name
        self.dimension = dimension

    def search_points(
        self,
        organization_id: UUID,
        versions: tuple[UUID, ...],
        vector: Embedding,
        filters: models.Filter,
        limit: int,
    ) -> SearchProjection:
        if not versions or not 1 <= limit <= 20:
            raise DocumentError("invalid_query")
        points = tuple(
            self.client.query_points(
                self.collection_name,
                prefetch=[
                    models.Prefetch(
                        query=list(vector.dense),
                        using="dense",
                        filter=filters,
                        limit=min(limit * 4, 80),
                        score_threshold=0.3,
                    ),
                    models.Prefetch(
                        query=models.SparseVector(
                            indices=list(vector.indices), values=list(vector.values)
                        ),
                        using="sparse",
                        filter=filters,
                        limit=min(limit * 4, 80),
                    ),
                ],
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                query_filter=filters,
                limit=limit,
                with_payload=True,
            ).points
        )
        return SearchProjection(points, points)

    def ensure_collection(self) -> None:
        if not self.client.collection_exists(self.collection_name):
            self.client.create_collection(
                self.collection_name,
                vectors_config={
                    "dense": models.VectorParams(
                        size=self.dimension, distance=models.Distance.COSINE
                    )
                },
                sparse_vectors_config={
                    "sparse": models.SparseVectorParams(modifier=models.Modifier.IDF)
                },
            )
        info = self.client.get_collection(self.collection_name)
        vectors = info.config.params.vectors
        sparse = info.config.params.sparse_vectors
        if (
            not isinstance(vectors, dict)
            or "dense" not in vectors
            or vectors["dense"].size != self.dimension
            or vectors["dense"].distance != models.Distance.COSINE
            or sparse is None
            or "sparse" not in sparse
            or sparse["sparse"].modifier != models.Modifier.IDF
        ):
            raise DocumentError("index_configuration_invalid", 503)
        required_types = dict.fromkeys(
            (
                "organization_id",
                "document_id",
                "document_version_id",
                "claim_id",
                "visibility",
                "allowed_user_ids",
                "allowed_group_ids",
            ),
            models.PayloadSchemaType.KEYWORD,
        )
        required_types["active"] = models.PayloadSchemaType.BOOL
        for key, expected in required_types.items():
            index = info.payload_schema.get(key)
            if index is not None and index.data_type != expected:
                raise DocumentError("index_configuration_invalid", 503)
            if key == "organization_id" and index is not None:
                if (
                    not isinstance(index.params, models.KeywordIndexParams)
                    or index.params.is_tenant is not True
                ):
                    raise DocumentError("index_configuration_invalid", 503)
        for key in (
            "organization_id",
            "document_id",
            "document_version_id",
            "claim_id",
            "visibility",
            "allowed_user_ids",
            "allowed_group_ids",
        ):
            if key not in info.payload_schema:
                schema = models.KeywordIndexParams(
                    type=models.KeywordIndexType.KEYWORD,
                    is_tenant=key == "organization_id",
                )
                self.client.create_payload_index(
                    self.collection_name, key, field_schema=schema, wait=True
                )
        if "active" not in info.payload_schema:
            self.client.create_payload_index(
                self.collection_name,
                "active",
                field_schema=models.PayloadSchemaType.BOOL,
                wait=True,
            )

    def stage(
        self,
        context: IndexContext,
        chunks: tuple[TextChunk, ...],
        provider: EmbeddingProvider,
    ) -> None:
        for start in range(0, len(chunks), 64):
            batch = chunks[start : start + 64]
            vectors = provider.documents([chunk.text for chunk in batch])
            points = []
            for chunk, vector in zip(batch, vectors, strict=True):
                points.append(
                    models.PointStruct(
                        id=str(uuid5(context.claim_id, str(chunk.id))),
                        vector={
                            "dense": list(vector.dense),
                            "sparse": models.SparseVector(
                                indices=list(vector.indices), values=list(vector.values)
                            ),
                        },
                        payload={
                            "organization_id": str(context.organization_id),
                            "document_id": str(context.document_id),
                            "document_version_id": str(context.version_id),
                            "claim_id": str(context.claim_id),
                            "chunk_id": str(chunk.id),
                            "chunk_index": chunk.index,
                            "filename": context.filename,
                            "location": chunk.location,
                            "text": chunk.text,
                            "visibility": context.visibility,
                            "allowed_user_ids": [
                                str(value) for value in context.user_ids
                            ],
                            "allowed_group_ids": [
                                str(value) for value in context.group_ids
                            ],
                            "active": False,
                        },
                    )
                )
            self.client.upsert(self.collection_name, points=points, wait=True)
        expected = self.client.count(
            self.collection_name, count_filter=self._claim_filter(context), exact=True
        ).count
        if expected != len(chunks):
            raise DocumentError("index_count_mismatch", 503)

    def _claim_filter(self, context: IndexContext) -> models.Filter:
        return models.Filter(
            must=[
                field_match("organization_id", str(context.organization_id)),
                field_match("document_version_id", str(context.version_id)),
                field_match("claim_id", str(context.claim_id)),
            ]
        )

    def activate(self, context: IndexContext) -> None:
        self.client.set_payload(
            self.collection_name,
            payload={"active": False},
            points=models.Filter(
                must=[
                    field_match("organization_id", str(context.organization_id)),
                    field_match("document_id", str(context.document_id)),
                ]
            ),
            wait=True,
        )

        self.client.set_payload(
            self.collection_name,
            payload={
                "active": True,
                "visibility": context.visibility,
                "allowed_user_ids": [str(value) for value in context.user_ids],
                "allowed_group_ids": [str(value) for value in context.group_ids],
            },
            points=self._claim_filter(context),
            wait=True,
        )

    def set_access(
        self,
        organization_id: UUID,
        document_id: UUID,
        visibility: str,
        users: tuple[UUID, ...],
        groups: tuple[UUID, ...],
    ) -> None:
        self.client.set_payload(
            self.collection_name,
            payload={
                "visibility": visibility,
                "allowed_user_ids": [str(value) for value in users],
                "allowed_group_ids": [str(value) for value in groups],
            },
            points=models.Filter(
                must=[
                    field_match("organization_id", str(organization_id)),
                    field_match("document_id", str(document_id)),
                ]
            ),
            wait=True,
        )

    def deactivate(self, organization_id: UUID, document_id: UUID) -> None:
        self.client.set_payload(
            self.collection_name,
            payload={"active": False},
            points=models.Filter(
                must=[
                    field_match("organization_id", str(organization_id)),
                    field_match("document_id", str(document_id)),
                ]
            ),
            wait=True,
        )
