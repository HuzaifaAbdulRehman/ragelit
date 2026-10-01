from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class AuthorizedChunk:
    id: UUID
    document_id: UUID
    version_id: UUID
    filename: str
    text: str
    location: str
    score: float
