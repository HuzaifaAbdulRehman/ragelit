from collections.abc import Mapping

from fastapi import Request
from fastapi.responses import JSONResponse


class ProblemException(Exception):
    def __init__(
        self,
        *,
        status: int,
        code: str,
        title: str,
        detail: str,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.headers = headers


def problem_response(
    *,
    status: int,
    code: str,
    title: str,
    detail: str,
    extensions: Mapping[str, object] | None = None,
    headers: Mapping[str, str] | None = None,
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
        headers=headers,
    )


async def problem_exception_handler(
    _request: Request,
    error: Exception,
) -> JSONResponse:
    if not isinstance(error, ProblemException):
        raise error
    return problem_response(
        status=error.status,
        code=error.code,
        title=error.title,
        detail=error.detail,
        headers=error.headers,
    )
