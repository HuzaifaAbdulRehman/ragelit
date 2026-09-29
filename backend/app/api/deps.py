from collections.abc import Iterator
from typing import Annotated, NoReturn

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.tenancy.scope import (
    AccessScope,
    PrincipalError,
    RequestPrincipal,
    load_access_scope,
    load_current_principal,
)


def get_database_session(request: Request) -> Iterator[Session]:
    factory = request.app.state.session_factory
    with factory() as session:
        yield session


DatabaseSession = Annotated[Session, Depends(get_database_session)]
AuthorizationHeader = Annotated[str | None, Header(alias="Authorization")]


def _authentication_failed() -> NoReturn:
    raise HTTPException(
        status_code=401,
        detail={
            "type": "about:blank",
            "title": "Unauthorized",
            "status": 401,
            "detail": "Authentication failed.",
            "code": "authentication_failed",
        },
        headers={"WWW-Authenticate": "Bearer"},
    )


def _access_token(authorization: AuthorizationHeader = None) -> str:
    if authorization is None:
        _authentication_failed()
    scheme, separator, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not separator or not token.strip():
        _authentication_failed()
    return token.strip()


AccessToken = Annotated[str, Depends(_access_token)]


def get_current_principal(
    request: Request,
    session: DatabaseSession,
    access_token: AccessToken,
) -> RequestPrincipal:
    settings: Settings = request.app.state.settings
    try:
        return load_current_principal(
            access_token,
            session=session,
            settings=settings,
        )
    except PrincipalError:
        _authentication_failed()


CurrentPrincipal = Annotated[RequestPrincipal, Depends(get_current_principal)]


def build_access_scope(
    principal: CurrentPrincipal,
    session: DatabaseSession,
) -> AccessScope:
    return load_access_scope(principal, session=session)


CurrentAccessScope = Annotated[AccessScope, Depends(build_access_scope)]
