from threading import Lock
from typing import Annotated, cast

from fastapi import Depends, Request

from app.chat.contracts import GenerationProvider
from app.chat.provider import CompatibleProvider
from app.retrieval.embeddings import Embedding, EmbeddingProvider, FastEmbedProvider


class LazyEmbeddings:
    dimension = 384

    def __init__(self, cache_dir: str) -> None:
        self._cache_dir, self._lock = cache_dir, Lock()
        self._provider: FastEmbedProvider | None = None

    def _get(self) -> FastEmbedProvider:
        with self._lock:
            if self._provider is None:
                self._provider = FastEmbedProvider(cache_dir=self._cache_dir)
            return self._provider

    def documents(self, texts: list[str]) -> list[Embedding]:
        return self._get().documents(texts)

    def query(self, text: str) -> Embedding:
        return self._get().query(text)


def get_embeddings(request: Request) -> EmbeddingProvider:
    if not hasattr(request.app.state, "embeddings"):
        request.app.state.embeddings = LazyEmbeddings(
            str(request.app.state.settings.data_dir / "models")
        )
    return cast(EmbeddingProvider, request.app.state.embeddings)


def get_generation_provider(request: Request) -> GenerationProvider:
    if not hasattr(request.app.state, "generation_provider"):
        request.app.state.generation_provider = CompatibleProvider(
            request.app.state.settings
        )
    return cast(GenerationProvider, request.app.state.generation_provider)


Embeddings = Annotated[EmbeddingProvider, Depends(get_embeddings)]
Generator = Annotated[GenerationProvider, Depends(get_generation_provider)]
