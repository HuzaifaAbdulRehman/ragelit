from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.retrieval.contracts import AuthorizedChunk


@dataclass(frozen=True, slots=True)
class Generation:
    answer: str
    citation_ids: tuple[UUID, ...]


class GenerationProvider(Protocol):
    def generate(
        self, question: str, context: tuple[AuthorizedChunk, ...]
    ) -> Generation: ...
