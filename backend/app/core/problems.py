from collections.abc import Mapping

from fastapi.responses import JSONResponse


def problem_response(
    *,
    status: int,
    code: str,
    title: str,
    detail: str,
    extensions: Mapping[str, object] | None = None,
) -> JSONResponse:
    content: dict[str, object] = {
        "type": "about:blank",
        "title": title,
        "status": status,
        "detail": detail,
        "code": code,
    }
    if extensions:
        content.update(extensions)

    return JSONResponse(
        status_code=status,
        content=content,
        media_type="application/problem+json",
    )
