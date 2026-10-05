from time import perf_counter
from uuid import UUID

from qdrant_client import models
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from sqlalchemy.orm import Session

from app.documents.extraction import DocumentError
from app.observability import ObservationSink
from app.retrieval.authorization import access_filter, current_scope, eligible_versions
from app.retrieval.contracts import AuthorizedChunk
from app.retrieval.embeddings import EmbeddingProvider
from app.retrieval.store import QdrantChunkStore
from app.tenancy.scope import AccessScope


class AuthorizedRetriever:
    def __init__(
        self, session: Session, store: QdrantChunkStore, provider: EmbeddingProvider
    ) -> None:
        self.session, self.store, self.provider = session, store, provider

    def search(
        self,
        scope: AccessScope,
        query: str,
        limit: int,
        *,
        observer: ObservationSink | None = None,
    ) -> tuple[AuthorizedChunk, ...]:
        if not query.strip() or len(query) > 4000 or not 1 <= limit <= 20:
            raise DocumentError("invalid_query")
        scope = current_scope(scope, self.session)
        if observer is not None:
            observer.scope(scope)
        versions = eligible_versions(scope, self.session)
        if not versions:
            if observer is not None:
                observer.retrieval_skipped()
            return ()
        filters = access_filter(scope, versions)
        started = perf_counter()
        try:
            vector = self.provider.query(query)
            points = self.store.client.query_points(
                self.store.collection_name,
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
        except (ResponseHandlingException, UnexpectedResponse, OSError) as error:
            raise DocumentError("retrieval_unavailable", 503) from error
        if observer is not None:
            observer.retrieval(points, (perf_counter() - started) * 1000)
        chunks: list[AuthorizedChunk] = []
        try:
            for point in points:
                payload = point.payload
                if (
                    payload is None
                    or payload["organization_id"] != str(scope.organization_id)
                    or UUID(payload["document_version_id"]) not in versions
                ):
                    raise DocumentError("invalid_retrieval_projection", 503)
                text, location, filename = (
                    str(payload["text"]),
                    str(payload["location"]),
                    str(payload["filename"]),
                )
                if (
                    not text.strip()
                    or len(text) > 1200
                    or len(location) > 100
                    or len(filename) > 255
                ):
                    raise DocumentError("invalid_retrieval_projection", 503)
                chunks.append(
                    AuthorizedChunk(
                        UUID(payload["chunk_id"]),
                        UUID(payload["document_id"]),
                        UUID(payload["document_version_id"]),
                        filename,
                        text,
                        location,
                        point.score,
                    )
                )
        except (KeyError, ValueError, TypeError) as error:
            raise DocumentError("invalid_retrieval_projection", 503) from error
        return tuple(chunks)
