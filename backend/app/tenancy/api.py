from typing import Annotated, Any, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import (
    CurrentPrincipal,
    DatabaseSession,
    require_organization_action,
)
from app.core.problems import ProblemDetail, ProblemException
from app.identity.models import User
from app.tenancy.models import Membership
from app.tenancy.policy import Action
from app.tenancy.schemas import (
    ActiveChange,
    GroupCreate,
    GroupList,
    GroupRename,
    GroupSummary,
    MemberList,
    MemberSummary,
    OrganizationList,
    OrganizationSummary,
    RoleChange,
)
from app.tenancy.scope import RequestPrincipal
from app.tenancy.service import (
    TenancyError,
    add_group_member,
    change_member_role,
    create_group,
    delete_group,
    list_group_members,
    list_groups,
    list_members,
    list_my_organizations,
    remove_group_member,
    rename_group,
    set_member_active,
)

router = APIRouter(tags=["tenancy"])

MemberManager = Annotated[
    RequestPrincipal,
    Depends(require_organization_action(Action.MEMBERS_MANAGE)),
]
GroupManager = Annotated[
    RequestPrincipal,
    Depends(require_organization_action(Action.GROUPS_MANAGE)),
]
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0)]

PROBLEM_CONTENT: dict[str, Any] = {
    "application/problem+json": {
        "schema": {"$ref": "#/components/schemas/ProblemDetail"},
    }
}
AUTH_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ProblemDetail, "content": PROBLEM_CONTENT},
}
TENANT_RESPONSES: dict[int | str, dict[str, Any]] = {
    **AUTH_RESPONSES,
    403: {"model": ProblemDetail, "content": PROBLEM_CONTENT},
    404: {"model": ProblemDetail, "content": PROBLEM_CONTENT},
}
TENANT_CONFLICT_RESPONSES: dict[int | str, dict[str, Any]] = {
    **TENANT_RESPONSES,
    409: {"model": ProblemDetail, "content": PROBLEM_CONTENT},
}


def _not_found() -> NoReturn:
    raise ProblemException(
        status=404,
        code="resource_not_found",
        title="Not Found",
        detail="The requested resource was not found.",
    )


def _raise_service_error(error: TenancyError) -> NoReturn:
    titles = {403: "Forbidden", 404: "Not Found", 409: "Conflict"}
    details = {
        "action_forbidden": "This action is not permitted.",
        "resource_not_found": "The requested resource was not found.",
        "last_owner_required": "The organization must keep an active owner.",
        "resource_conflict": "The requested change conflicts with current state.",
    }
    raise ProblemException(
        status=error.status,
        code=error.code,
        title=titles[error.status],
        detail=details[error.code],
    ) from error


def _member_summary(membership: Membership, user: User) -> MemberSummary:
    return MemberSummary(
        id=membership.id,
        user_id=user.id,
        email=user.email,
        role=membership.role,
        is_active=membership.is_active,
    )


def _member_user(membership: Membership, session: DatabaseSession) -> User:
    user = session.get(User, membership.user_id)
    if user is None:
        _not_found()
    return user


@router.get("/organizations", responses=AUTH_RESPONSES)
def organizations_route(
    principal: CurrentPrincipal,
    session: DatabaseSession,
) -> OrganizationList:
    rows = list_my_organizations(principal, session=session)
    return OrganizationList(
        items=[
            OrganizationSummary(
                id=organization.id,
                name=organization.name,
                slug=organization.slug,
                role=membership.role,
            )
            for membership, organization in rows
        ]
    )


@router.get(
    "/organizations/{organization_id}/members",
    responses=TENANT_RESPONSES,
)
def members_route(
    organization_id: UUID,
    principal: MemberManager,
    session: DatabaseSession,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
) -> MemberList:
    rows = list_members(principal, session=session, limit=limit, offset=offset)
    return MemberList(
        items=[_member_summary(membership, user) for membership, user in rows],
        limit=limit,
        offset=offset,
    )


@router.patch(
    "/organizations/{organization_id}/members/{membership_id}/role",
    responses=TENANT_CONFLICT_RESPONSES,
)
def change_member_role_route(
    organization_id: UUID,
    membership_id: UUID,
    command: RoleChange,
    principal: MemberManager,
    session: DatabaseSession,
) -> MemberSummary:
    try:
        membership = change_member_role(
            principal,
            membership_id,
            command.role,
            session=session,
        )
    except TenancyError as error:
        _raise_service_error(error)
    return _member_summary(membership, _member_user(membership, session))


@router.patch(
    "/organizations/{organization_id}/members/{membership_id}/active",
    responses=TENANT_CONFLICT_RESPONSES,
)
def set_member_active_route(
    organization_id: UUID,
    membership_id: UUID,
    command: ActiveChange,
    principal: MemberManager,
    session: DatabaseSession,
) -> MemberSummary:
    try:
        membership = set_member_active(
            principal,
            membership_id,
            command.is_active,
            session=session,
        )
    except TenancyError as error:
        _raise_service_error(error)
    return _member_summary(membership, _member_user(membership, session))


@router.get(
    "/organizations/{organization_id}/groups",
    responses=TENANT_RESPONSES,
)
def groups_route(
    organization_id: UUID,
    principal: GroupManager,
    session: DatabaseSession,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
) -> GroupList:
    groups = list_groups(principal, session=session, limit=limit, offset=offset)
    return GroupList(
        items=[GroupSummary.model_validate(group) for group in groups],
        limit=limit,
        offset=offset,
    )


@router.post(
    "/organizations/{organization_id}/groups",
    status_code=201,
    responses=TENANT_CONFLICT_RESPONSES,
)
def create_group_route(
    organization_id: UUID,
    command: GroupCreate,
    principal: GroupManager,
    session: DatabaseSession,
) -> GroupSummary:
    try:
        group = create_group(principal, command.name, session=session)
    except TenancyError as error:
        _raise_service_error(error)
    return GroupSummary.model_validate(group)


@router.patch(
    "/organizations/{organization_id}/groups/{group_id}",
    responses=TENANT_CONFLICT_RESPONSES,
)
def rename_group_route(
    organization_id: UUID,
    group_id: UUID,
    command: GroupRename,
    principal: GroupManager,
    session: DatabaseSession,
) -> GroupSummary:
    try:
        group = rename_group(
            principal,
            group_id,
            command.name,
            session=session,
        )
    except TenancyError as error:
        _raise_service_error(error)
    return GroupSummary.model_validate(group)


@router.delete(
    "/organizations/{organization_id}/groups/{group_id}",
    status_code=204,
    responses=TENANT_RESPONSES,
)
def delete_group_route(
    organization_id: UUID,
    group_id: UUID,
    principal: GroupManager,
    session: DatabaseSession,
) -> Response:
    try:
        delete_group(principal, group_id, session=session)
    except TenancyError as error:
        _raise_service_error(error)
    return Response(status_code=204)


@router.post(
    "/organizations/{organization_id}/groups/{group_id}/members/{membership_id}",
    status_code=204,
    responses=TENANT_CONFLICT_RESPONSES,
)
def add_group_member_route(
    organization_id: UUID,
    group_id: UUID,
    membership_id: UUID,
    principal: GroupManager,
    session: DatabaseSession,
) -> Response:
    try:
        add_group_member(
            principal,
            group_id,
            membership_id,
            session=session,
        )
    except TenancyError as error:
        _raise_service_error(error)
    return Response(status_code=204)


@router.get(
    "/organizations/{organization_id}/groups/{group_id}/members",
    responses=TENANT_RESPONSES,
)
def group_members_route(
    organization_id: UUID,
    group_id: UUID,
    principal: GroupManager,
    session: DatabaseSession,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
) -> MemberList:
    try:
        rows = list_group_members(
            principal, group_id, session=session, limit=limit, offset=offset
        )
    except TenancyError as error:
        _raise_service_error(error)
    return MemberList(
        items=[_member_summary(membership, user) for membership, user in rows],
        limit=limit,
        offset=offset,
    )


@router.delete(
    "/organizations/{organization_id}/groups/{group_id}/members/{membership_id}",
    status_code=204,
    responses=TENANT_RESPONSES,
)
def remove_group_member_route(
    organization_id: UUID,
    group_id: UUID,
    membership_id: UUID,
    principal: GroupManager,
    session: DatabaseSession,
) -> Response:
    try:
        remove_group_member(
            principal,
            group_id,
            membership_id,
            session=session,
        )
    except TenancyError as error:
        _raise_service_error(error)
    return Response(status_code=204)
