from collections.abc import Iterator
from uuid import uuid4

import pytest
from qdrant_client import QdrantClient

from app.retrieval.embeddings import Embedding
from app.retrieval.store import QdrantChunkStore


class TestEmbeddings:
    dimension = 4

    def documents(self, texts: list[str]) -> list[Embedding]:
        return [self.query(text) for text in texts]

    def query(self, text: str) -> Embedding:
        return Embedding((1.0, 0.0, 0.0, 0.0), (1,), (1.0,))


@pytest.fixture
def vector_store() -> Iterator[QdrantChunkStore]:
    client = QdrantClient(url="http://127.0.0.1:6333", timeout=5)
    name = f"ragelit_test_{uuid4().hex}"
    store = QdrantChunkStore(client, name, dimension=4)
    store.ensure_collection()
    try:
        yield store
    finally:
        client.delete_collection(name)
        client.close()
