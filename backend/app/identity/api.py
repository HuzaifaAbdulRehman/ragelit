from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Request, Response
from fastapi.responses import JSONResponse

from app.api.deps import CurrentPrincipal, DatabaseSession
from app.core.config import Settings
from app.core.problems import ProblemDetail, problem_response
from app.identity.schemas import (
    AccessTokenResponse,
    LoginCommand,
    SwitchOrganizationCommand,
)
from app.identity.service import (
    AuthError,
    login,
    logout_refresh,
    refresh,
    switch_organization,
)

REFRESH_COOKIE = "ragelit_refresh"

router = APIRouter(prefix="/auth", tags=["authentication"])

PROBLEM_CONTENT: dict[str, Any] = {
    "application/problem+json": {
        "schema": {"$ref": "#/components/schemas/ProblemDetail"},
    }
}
AUTH_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ProblemDetail, "content": PROBLEM_CONTENT},
}
SWITCH_RESPONSES: dict[int | str, dict[str, Any]] = {
    **AUTH_RESPONSES,
    404: {"model": ProblemDetail, "content": PROBLEM_CONTENT},
}


RefreshCookie = Annotated[str | None, Cookie(alias=REFRESH_COOKIE)]


def _token_response(access_token: str) -> JSONResponse:
    return JSONResponse(
        {"access_token": access_token, "token_type": "bearer"},
        headers={"Cache-Control": "no-store"},
    )


def _set_refresh_cookie(
    response: Response,
    raw_token: str,
    settings: Settings,
) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=raw_token,
        max_age=settings.refresh_session_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/api/v1/auth",
    )


def _auth_problem(code: str) -> JSONResponse:
    detail = (
        "Authentication failed."
        if code == "invalid_credentials"
        else "Session is invalid."
    )
    return problem_response(
        status=401,
        code=code,
        title="Unauthorized",
        detail=detail,
    )


@router.post(
    "/login",
    response_model=AccessTokenResponse,
    responses=AUTH_RESPONSES,
)
def login_route(
    command: LoginCommand,
    request: Request,
    session: DatabaseSession,
) -> JSONResponse:
    settings: Settings = request.app.state.settings
    try:
        result = login(command, session=session, settings=settings)
    except AuthError as error:
        return _auth_problem(error.code)

    response = _token_response(result.access_token)
    _set_refresh_cookie(response, result.refresh_token, settings)
    return response


@router.post(
    "/refresh",
    response_model=AccessTokenResponse,
    responses=AUTH_RESPONSES,
)
def refresh_route(
    request: Request,
    session: DatabaseSession,
    raw_token: RefreshCookie = None,
) -> JSONResponse:
    if raw_token is None:
        return _auth_problem("invalid_refresh")
    settings: Settings = request.app.state.settings
    try:
        result = refresh(raw_token, session=session, settings=settings)
    except AuthError as error:
        return _auth_problem(error.code)

    response = _token_response(result.access_token)
    _set_refresh_cookie(response, result.refresh_token, settings)
    return response


@router.post("/logout", status_code=204, responses=AUTH_RESPONSES)
def logout_route(
    request: Request,
    session: DatabaseSession,
    raw_token: RefreshCookie = None,
) -> Response:
    if raw_token is not None:
        logout_refresh(raw_token, session=session)
    response = Response(status_code=204)
    response.delete_cookie(key=REFRESH_COOKIE, path="/api/v1/auth")
    return response


@router.post(
    "/switch-organization",
    response_model=AccessTokenResponse,
    responses=SWITCH_RESPONSES,
)
def switch_organization_route(
    command: SwitchOrganizationCommand,
    request: Request,
    principal: CurrentPrincipal,
    session: DatabaseSession,
) -> JSONResponse:
    settings: Settings = request.app.state.settings
    try:
        result = switch_organization(
            principal,
            command.target_organization_id,
            session=session,
            settings=settings,
        )
    except AuthError as error:
        if error.code == "resource_not_found":
            return problem_response(
                status=404,
                code="resource_not_found",
                title="Not Found",
                detail="The requested resource was not found.",
            )
        return _auth_problem(error.code)

    response = _token_response(result.access_token)
    _set_refresh_cookie(response, result.refresh_token, settings)
    return response
