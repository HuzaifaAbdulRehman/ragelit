from app.retrieval.contracts import AuthorizedChunk


def build_context(
    chunks: tuple[AuthorizedChunk, ...], *, max_chars: int = 16000
) -> tuple[AuthorizedChunk, ...]:
    context: list[AuthorizedChunk] = []
    used = 0
    for chunk in chunks:
        if not isinstance(chunk, AuthorizedChunk):
            raise TypeError("context requires authorized chunks")
        cost = len(chunk.text) + len(chunk.filename) + len(chunk.location) + 100
        if used + cost > max_chars:
            break
        context.append(chunk)
        used += cost
    return tuple(context)
