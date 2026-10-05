from app.retrieval.embeddings import Embedding


class MissEmbeddings:
    dimension = 384
    fingerprint = "1" * 64

    def documents(self, texts: list[str]) -> list[Embedding]:
        return [Embedding((1.0, *(0.0,) * 383), (3,), (1.0,)) for _ in texts]

    def query(self, text: str) -> Embedding:
        return Embedding((0.0, 1.0, *(0.0,) * 382), (9,), (1.0,))
