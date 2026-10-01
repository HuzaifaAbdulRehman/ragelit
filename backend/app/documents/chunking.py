from dataclasses import dataclass
from uuid import UUID, uuid5

from app.documents.extraction import TextSection


@dataclass(frozen=True, slots=True)
class TextChunk:
    id: UUID
    index: int
    text: str
    location: str


def chunk_sections(
    sections: tuple[TextSection, ...],
    version_id: UUID,
    *,
    max_chars: int = 1200,
    overlap: int = 160,
) -> tuple[TextChunk, ...]:
    if max_chars <= 0 or not 0 <= overlap < max_chars:
        raise ValueError("invalid chunk limits")
    chunks: list[TextChunk] = []
    for section in sections:
        text = section.text.strip()
        start = 0
        while start < len(text):
            end = min(start + max_chars, len(text))
            if end < len(text):
                boundary = max(
                    text.rfind("\n", start + max_chars // 2, end),
                    text.rfind(" ", start + max_chars // 2, end),
                )
                if boundary > start:
                    end = boundary
            content = text[start:end].strip()
            if content:
                index = len(chunks)
                chunks.append(
                    TextChunk(
                        uuid5(version_id, str(index)), index, content, section.location
                    )
                )
            if end == len(text):
                break
            start = max(start + 1, end - overlap)
    return tuple(chunks)
