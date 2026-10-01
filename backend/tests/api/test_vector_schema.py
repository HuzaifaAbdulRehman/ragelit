import pytest
from qdrant_client import models

from app.documents.extraction import DocumentError
from app.retrieval.store import QdrantChunkStore


@pytest.mark.parametrize("fault", ["distance", "idf", "index_type", "tenant"])
def test_existing_incompatible_collection_is_rejected(
    vector_store: QdrantChunkStore, fault: str
) -> None:
    client, name = vector_store.client, vector_store.collection_name
    client.delete_collection(name)
    client.create_collection(
        name,
        vectors_config={
            "dense": models.VectorParams(
                size=4,
                distance=models.Distance.EUCLID
                if fault == "distance"
                else models.Distance.COSINE,
            )
        },
        sparse_vectors_config={
            "sparse": models.SparseVectorParams(
                modifier=None if fault == "idf" else models.Modifier.IDF
            )
        },
    )
    if fault == "index_type":
        client.create_payload_index(
            name, "active", field_schema=models.PayloadSchemaType.INTEGER, wait=True
        )
    if fault == "tenant":
        client.create_payload_index(
            name,
            "organization_id",
            field_schema=models.KeywordIndexParams(
                type=models.KeywordIndexType.KEYWORD, is_tenant=False
            ),
            wait=True,
        )
    with pytest.raises(DocumentError) as caught:
        vector_store.ensure_collection()
    assert caught.value.code == "index_configuration_invalid"
