from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.identity.models import User
from app.tenancy.enums import Role
from app.tenancy.models import Group, GroupMember, Membership, Organization
from app.tenancy.scope import RequestPrincipal


class TenancyError(Exception):
    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


def _not_found() -> TenancyError:
    return TenancyError("resource_not_found", 404)


def _forbidden() -> TenancyError:
    return TenancyError("action_forbidden", 403)


def list_my_organizations(
    principal: RequestPrincipal,
    *,
    session: Session,
) -> list[tuple[Membership, Organization]]:
    rows = session.execute(
        select(Membership, Organization)
        .join(Organization, Organization.id == Membership.organization_id)
        .where(
            Membership.user_id == principal.user_id,
            Membership.is_active.is_(True),
        )
        .order_by(Organization.name, Organization.id)
    )
    return [(membership, organization) for membership, organization in rows]


def list_members(
    principal: RequestPrincipal,
    *,
    session: Session,
    limit: int,
    offset: int,
) -> list[tuple[Membership, User]]:
    rows = session.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.organization_id == principal.organization_id)
        .order_by(User.email, Membership.id)
        .limit(limit)
        .offset(offset)
    )
    return [(membership, user) for membership, user in rows]


def _locked_membership(
    principal: RequestPrincipal,
    membership_id: UUID,
    *,
    session: Session,
) -> Membership:
    membership = session.scalar(
        select(Membership)
        .where(
            Membership.id == membership_id,
            Membership.organization_id == principal.organization_id,
        )
        .with_for_update()
    )
    if membership is None:
        raise _not_found()
    return membership


def _lock_organization(
    principal: RequestPrincipal,
    *,
    session: Session,
) -> None:
    organization_id = session.scalar(
        select(Organization.id)
        .where(Organization.id == principal.organization_id)
        .with_for_update()
    )
    if organization_id is None:
        raise _not_found()


def _ensure_owner_remains(
    principal: RequestPrincipal,
    membership: Membership,
    *,
    session: Session,
) -> None:
    if membership.role is not Role.OWNER or not membership.is_active:
        return
    active_owners = list(
        session.scalars(
            select(Membership)
            .where(
                Membership.organization_id == principal.organization_id,
                Membership.role == Role.OWNER,
                Membership.is_active.is_(True),
            )
            .order_by(Membership.id)
            .with_for_update()
        )
    )
    if len(active_owners) <= 1:
        raise TenancyError("last_owner_required", 409)


def change_member_role(
    principal: RequestPrincipal,
    membership_id: UUID,
    role: Role,
    *,
    session: Session,
) -> Membership:
    _lock_organization(principal, session=session)
    membership = _locked_membership(principal, membership_id, session=session)
    if principal.role is Role.ADMIN and (
        membership.role is Role.OWNER or role is Role.OWNER
    ):
        raise _forbidden()
    if membership.role is Role.OWNER and role is not Role.OWNER:
        _ensure_owner_remains(principal, membership, session=session)
    membership.role = role
    session.commit()
    return membership


def set_member_active(
    principal: RequestPrincipal,
    membership_id: UUID,
    is_active: bool,
    *,
    session: Session,
) -> Membership:
    _lock_organization(principal, session=session)
    membership = _locked_membership(principal, membership_id, session=session)
    if principal.role is Role.ADMIN and membership.role is Role.OWNER:
        raise _forbidden()
    if membership.is_active and not is_active:
        _ensure_owner_remains(principal, membership, session=session)
    membership.is_active = is_active
    session.commit()
    return membership


def list_groups(
    principal: RequestPrincipal,
    *,
    session: Session,
    limit: int,
    offset: int,
) -> list[Group]:
    return list(
        session.scalars(
            select(Group)
            .where(Group.organization_id == principal.organization_id)
            .order_by(Group.name, Group.id)
            .limit(limit)
            .offset(offset)
        )
    )


def create_group(
    principal: RequestPrincipal,
    name: str,
    *,
    session: Session,
) -> Group:
    _lock_organization(principal, session=session)
    group = Group(organization_id=principal.organization_id, name=name)
    session.add(group)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise TenancyError("resource_conflict", 409) from error
    return group


def list_group_members(
    principal: RequestPrincipal,
    group_id: UUID,
    *,
    session: Session,
    limit: int = 50,
    offset: int = 0,
) -> list[tuple[Membership, User]]:
    group = session.scalar(
        select(Group).where(
            Group.id == group_id,
            Group.organization_id == principal.organization_id,
        )
    )
    if group is None:
        raise _not_found()
    rows = session.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .join(
            GroupMember,
            (GroupMember.user_id == Membership.user_id)
            & (GroupMember.organization_id == Membership.organization_id),
        )
        .where(
            Membership.organization_id == principal.organization_id,
            GroupMember.group_id == group_id,
        )
        .order_by(User.email, Membership.id)
        .limit(limit)
        .offset(offset)
    )
    return [(membership, user) for membership, user in rows]


def _locked_group(
    principal: RequestPrincipal,
    group_id: UUID,
    *,
    session: Session,
) -> Group:
    group = session.scalar(
        select(Group)
        .where(
            Group.id == group_id,
            Group.organization_id == principal.organization_id,
        )
        .with_for_update()
    )
    if group is None:
        raise _not_found()
    return group


def rename_group(
    principal: RequestPrincipal,
    group_id: UUID,
    name: str,
    *,
    session: Session,
) -> Group:
    _lock_organization(principal, session=session)
    group = _locked_group(principal, group_id, session=session)
    group.name = name
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise TenancyError("resource_conflict", 409) from error
    return group


def delete_group(
    principal: RequestPrincipal,
    group_id: UUID,
    *,
    session: Session,
) -> None:
    _lock_organization(principal, session=session)
    group = _locked_group(principal, group_id, session=session)
    session.delete(group)
    session.commit()


def add_group_member(
    principal: RequestPrincipal,
    group_id: UUID,
    membership_id: UUID,
    *,
    session: Session,
) -> None:
    _lock_organization(principal, session=session)
    group = _locked_group(principal, group_id, session=session)
    membership = _locked_membership(principal, membership_id, session=session)
    if not membership.is_active:
        raise _not_found()
    session.add(
        GroupMember(
            organization_id=principal.organization_id,
            group_id=group.id,
            user_id=membership.user_id,
        )
    )
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise TenancyError("resource_conflict", 409) from error


def remove_group_member(
    principal: RequestPrincipal,
    group_id: UUID,
    membership_id: UUID,
    *,
    session: Session,
) -> None:
    _lock_organization(principal, session=session)
    group = _locked_group(principal, group_id, session=session)
    membership = _locked_membership(principal, membership_id, session=session)
    group_member = session.scalar(
        select(GroupMember)
        .where(
            GroupMember.organization_id == principal.organization_id,
            GroupMember.group_id == group.id,
            GroupMember.user_id == membership.user_id,
        )
        .with_for_update()
    )
    if group_member is None:
        raise _not_found()
    session.delete(group_member)
    session.commit()
