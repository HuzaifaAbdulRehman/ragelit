from uuid import uuid4

import pytest

from app.chat.context import build_context
from app.retrieval.contracts import AuthorizedChunk


def test_context_budget_keeps_only_complete_authorized_chunks() -> None:
    chunks = tuple(
        AuthorizedChunk(
            uuid4(), uuid4(), uuid4(), "notes.txt", "evidence " * 100, "page 1", 0.8
        )
        for _ in range(30)
    )
    context = build_context(chunks, max_chars=2000)
    assert context and len(context) < len(chunks)
    assert (
        sum(
            len(chunk.text) + len(chunk.filename) + len(chunk.location) + 100
            for chunk in context
        )
        <= 2000
    )
    assert all(chunk in chunks for chunk in context)


def test_context_rejects_raw_unreviewed_candidates() -> None:
    with pytest.raises(TypeError):
        build_context((object(),))  # type: ignore[arg-type]
