from collections.abc import Sequence
from typing import Literal, Protocol
from uuid import UUID

from qdrant_client import models

from app.retrieval.contracts import AuthorizedChunk
from app.tenancy.scope import AccessScope


class ObservationSink(Protocol):
    def scope(self, scope: AccessScope) -> None: ...

    def retrieval(
        self, points: Sequence[models.ScoredPoint], duration_ms: float
    ) -> None: ...

    def retrieval_skipped(self) -> None: ...

    def chunks(
        self,
        boundary: Literal["retrieval_accepted", "context"],
        chunks: tuple[AuthorizedChunk, ...],
        duration_ms: float,
    ) -> None: ...

    def generation(
        self, answer: str, citation_ids: tuple[UUID, ...], duration_ms: float
    ) -> None: ...

    def delivery(self, answer: str | None, citation_ids: tuple[UUID, ...]) -> None: ...

    def finish(self, http_status: int, state: str, code: str | None = None) -> None: ...
