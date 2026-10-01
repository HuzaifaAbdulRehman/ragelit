from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Embedding:
    dense: tuple[float, ...]
    indices: tuple[int, ...]
    values: tuple[float, ...]


class EmbeddingProvider(Protocol):
    dimension: int

    def documents(self, texts: list[str]) -> list[Embedding]: ...

    def query(self, text: str) -> Embedding: ...


class FastEmbedProvider:
    dimension = 384

    def __init__(self, *, cache_dir: str) -> None:
        from fastembed import SparseTextEmbedding, TextEmbedding

        self._dense = TextEmbedding(
            "BAAI/bge-small-en-v1.5", cache_dir=cache_dir, threads=2
        )
        self._sparse = SparseTextEmbedding(
            "Qdrant/bm25", cache_dir=cache_dir, threads=2
        )

    def documents(self, texts: list[str]) -> list[Embedding]:
        dense = list(self._dense.passage_embed(texts))
        sparse = list(self._sparse.passage_embed(texts))
        return [
            Embedding(
                tuple(float(value) for value in vector),
                tuple(int(index) for index in keyword.indices),
                tuple(float(value) for value in keyword.values),
            )
            for vector, keyword in zip(dense, sparse, strict=True)
        ]

    def query(self, text: str) -> Embedding:
        dense = next(iter(self._dense.query_embed(text)))
        sparse = next(iter(self._sparse.query_embed(text)))
        return Embedding(
            tuple(float(value) for value in dense),
            tuple(int(index) for index in sparse.indices),
            tuple(float(value) for value in sparse.values),
        )
