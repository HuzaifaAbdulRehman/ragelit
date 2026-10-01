from typing import Annotated, cast

from fastapi import Depends, Request
from qdrant_client import QdrantClient

from app.retrieval.store import QdrantChunkStore


def get_chunk_store(request: Request) -> QdrantChunkStore:
    if not hasattr(request.app.state, "chunk_store"):
        settings = request.app.state.settings
        request.app.state.chunk_store = QdrantChunkStore(
            QdrantClient(url=str(settings.qdrant_url), timeout=10),
            settings.qdrant_collection,
            dimension=384,
        )
    return cast(QdrantChunkStore, request.app.state.chunk_store)


ChunkStore = Annotated[QdrantChunkStore, Depends(get_chunk_store)]
