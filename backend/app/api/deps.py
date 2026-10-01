from collections.abc import Callable, Iterator
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.problems import ProblemException
from app.tenancy.policy import Action, role_allows
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
_bearer_scheme = HTTPBearer(auto_error=False)
BearerCredentials = Annotated[
    HTTPAuthorizationCredentials | None,
    Depends(_bearer_scheme),
]


def _authentication_failed(code: str = "authentication_failed") -> NoReturn:
    detail = (
        "Membership is inactive."
        if code == "membership_inactive"
        else "Authentication failed."
    )
    raise ProblemException(
        status=401,
        code=code,
        title="Unauthorized",
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _access_token(credentials: BearerCredentials = None) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        _authentication_failed()
    token = credentials.credentials.strip()
    if not token:
        _authentication_failed()
    return token


AccessToken = Annotated[str, Depends(_access_token)]


def get_current_principal(
    request: Request,
    access_token: AccessToken,
    session: DatabaseSession,
) -> RequestPrincipal:
    settings: Settings = request.app.state.settings
    try:
        return load_current_principal(
            access_token,
            session=session,
            settings=settings,
        )
    except PrincipalError as error:
        _authentication_failed(error.code)


CurrentPrincipal = Annotated[RequestPrincipal, Depends(get_current_principal)]


def get_optional_current_principal(
    request: Request,
    session: DatabaseSession,
    credentials: BearerCredentials = None,
) -> RequestPrincipal | None:
    if credentials is None or credentials.scheme.lower() != "bearer":
        return None
    token = credentials.credentials.strip()
    if not token:
        return None
    settings: Settings = request.app.state.settings
    try:
        return load_current_principal(token, session=session, settings=settings)
    except PrincipalError:
        return None


OptionalCurrentPrincipal = Annotated[
    RequestPrincipal | None,
    Depends(get_optional_current_principal),
]


def build_access_scope(
    principal: CurrentPrincipal,
    session: DatabaseSession,
) -> AccessScope:
    return load_access_scope(principal, session=session)


CurrentAccessScope = Annotated[AccessScope, Depends(build_access_scope)]


def require_action(action: Action) -> Callable[..., RequestPrincipal]:
    def guard(principal: CurrentPrincipal) -> RequestPrincipal:
        if not role_allows(principal.role, action):
            raise ProblemException(
                status=403,
                code="action_forbidden",
                title="Forbidden",
                detail="This action is not permitted.",
            )
        return principal

    return guard


def require_organization_action(action: Action) -> Callable[..., RequestPrincipal]:
    def guard(
        organization_id: UUID,
        principal: CurrentPrincipal,
    ) -> RequestPrincipal:
        if organization_id != principal.organization_id:
            raise ProblemException(
                status=404,
                code="resource_not_found",
                title="Not Found",
                detail="The requested resource was not found.",
            )
        if not role_allows(principal.role, action):
            raise ProblemException(
                status=403,
                code="action_forbidden",
                title="Forbidden",
                detail="This action is not permitted.",
            )
        return principal

    return guard
