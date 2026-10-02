from fastapi import FastAPI
from qdrant_client import QdrantClient
from sqlalchemy.engine import make_url

from app.chat.contracts import Generation
from app.core.config import Settings
from app.main import create_app as create_real_app
from app.retrieval.contracts import AuthorizedChunk
from app.retrieval.embeddings import Embedding
from app.retrieval.store import QdrantChunkStore


def validate_fixture_settings(settings: Settings) -> None:
    urls = [make_url(settings.database_url), make_url(settings.database_admin_url)]
    if (
        settings.environment != "test"
        or settings.qdrant_collection != "ragelit_e2e"
        or any(url.database != "ragelit_e2e" for url in urls)
        or any(url.host != "127.0.0.1" or url.port != 5432 for url in urls)
        or str(settings.qdrant_url).rstrip("/") != "http://127.0.0.1:6333"
    ):
        raise RuntimeError("E2E fixtures require dedicated local test targets")


class FixtureEmbeddings:
    dimension = 4

    def documents(self, texts: list[str]) -> list[Embedding]:
        return [self.query(text) for text in texts]

    def query(self, text: str) -> Embedding:
        return Embedding((1.0, 0.0, 0.0, 0.0), (1,), (1.0,))


class FixtureGeneration:
    def generate(
        self, question: str, context: tuple[AuthorizedChunk, ...]
    ) -> Generation:
        return Generation(context[0].text, (context[0].id,))


def fixture_store(settings: Settings) -> QdrantChunkStore:
    validate_fixture_settings(settings)
    return QdrantChunkStore(
        QdrantClient(url=str(settings.qdrant_url), timeout=10),
        "ragelit_e2e",
        dimension=FixtureEmbeddings.dimension,
    )


def create_app() -> FastAPI:
    settings = Settings()  # type: ignore[call-arg]
    validate_fixture_settings(settings)
    app = create_real_app(settings)
    app.state.embeddings = FixtureEmbeddings()
    app.state.generation_provider = FixtureGeneration()
    app.state.chunk_store = fixture_store(settings)
    return app
