from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID

import pytest
from qdrant_client import QdrantClient, models

from app.documents.chunking import TextChunk
from app.documents.extraction import DocumentError
from app.retrieval.embeddings import Embedding
from app.retrieval.store import IndexContext, QdrantChunkStore

pytestmark = pytest.mark.filterwarnings(
    "ignore:Payload indexes have no effect:UserWarning"
)

ORG = UUID(int=1)
VERSION = UUID(int=30)
CHUNK = UUID(int=40)
DENSE = Embedding((1.0, 0.0, 0.0, 0.0), (7,), (1.0,))


@dataclass
class FixedEmbeddings:
    vector: Embedding
    dimension: int = 4

    def documents(self, texts: list[str]) -> list[Embedding]:
        return [self.vector for _ in texts]

    def query(self, text: str) -> Embedding:
        return self.vector


def index(
    store: QdrantChunkStore,
    number: int,
    organization: UUID,
    *,
    active: bool = True,
    vector: Embedding = DENSE,
) -> None:
    context = IndexContext(
        organization,
        UUID(int=number + 20),
        UUID(int=number + 30),
        UUID(int=number + 50),
        "synthetic.txt",
        "organization",
        (),
        (),
    )
    store.stage(
        context,
        (TextChunk(UUID(int=number + 40), 0, "synthetic evidence", "line 1"),),
        FixedEmbeddings(vector),
    )
    if active:
        store.activate(context)


def filters(versions: tuple[UUID, ...] = (VERSION,)) -> models.Filter:
    return models.Filter(
        must=[
            models.FieldCondition(
                key="organization_id", match=models.MatchValue(value=str(ORG))
            ),
            models.FieldCondition(key="active", match=models.MatchValue(value=True)),
            models.FieldCondition(
                key="document_version_id",
                match=models.MatchAny(any=[str(version) for version in versions]),
            ),
        ]
    )


@pytest.fixture
def store() -> Iterator[QdrantChunkStore]:
    client = QdrantClient(":memory:")
    result = QdrantChunkStore(client, "owned_unit_search", dimension=4)
    try:
        result.ensure_collection()
        yield result
    finally:
        client.close()


def test_shared_search_keeps_authorized_positive_and_excludes_foreign_inactive(
    store: QdrantChunkStore,
) -> None:
    index(store, 0, ORG)
    index(store, 1, UUID(int=2))
    index(store, 2, ORG, active=False)
    permitted_versions = (VERSION, UUID(int=31), UUID(int=32))
    result = store.search_points(
        ORG,
        permitted_versions,
        DENSE,
        filters(permitted_versions),
        10,
    )
    assert len(result.raw) == len(result.accepted) == 1
    assert [point.payload["chunk_id"] for point in result.raw if point.payload] == [
        str(CHUNK)
    ]
    assert result.raw == result.accepted


def test_sparse_branch_keeps_a_match_below_dense_threshold(
    store: QdrantChunkStore,
) -> None:
    index(store, 0, ORG, vector=Embedding((0.0, 1.0, 0.0, 0.0), (7,), (1.0,)))
    result = store.search_points(ORG, (VERSION,), DENSE, filters(), 10)
    assert len(result.raw) == len(result.accepted) == 1
    assert result.accepted[0].payload is not None
    assert result.accepted[0].payload["chunk_id"] == str(CHUNK)


@pytest.mark.parametrize(
    "versions,limit", [((), 10), ((VERSION,), 0), ((VERSION,), 21)]
)
def test_invalid_search_does_not_read_an_unfiltered_collection(
    store: QdrantChunkStore,
    versions: tuple[UUID, ...],
    limit: int,
) -> None:
    index(store, 0, ORG)
    with pytest.raises(DocumentError) as stopped:
        store.search_points(ORG, versions, DENSE, filters(), limit)
    assert stopped.value.code == "invalid_query"
