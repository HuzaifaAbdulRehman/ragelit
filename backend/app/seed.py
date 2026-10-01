from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import hash_password
from app.db.session import build_engine
from app.identity.models import User
from app.tenancy.enums import Role
from app.tenancy.models import Group, Membership, Organization


@dataclass(frozen=True, slots=True)
class DemoManifest:
    organization_slugs: tuple[str, ...]
    user_count: int
    membership_count: int
    group_count: int


@dataclass(frozen=True, slots=True)
class _UserSpec:
    email: str
    role: Role


_ORGANIZATIONS = (
    ("harbor-works", "Harbor Works", ("Finance", "Operations")),
    ("northstar-labs", "Northstar Labs", ("Engineering", "Research")),
)
_NORTHSTAR_USERS = (
    _UserSpec("admin@northstar.example", Role.ADMIN),
    _UserSpec("auditor@northstar.example", Role.AUDITOR),
    _UserSpec("member@northstar.example", Role.MEMBER),
    _UserSpec("owner@northstar.example", Role.OWNER),
)


def _organization(
    session: Session,
    *,
    slug: str,
    name: str,
) -> Organization:
    organization = session.scalar(select(Organization).where(Organization.slug == slug))
    if organization is None:
        organization = Organization(slug=slug, name=name)
        session.add(organization)
        session.flush()
    return organization


def _user(
    session: Session,
    email: str,
    password_factory: Callable[[], str],
) -> tuple[User, str | None]:
    user = session.scalar(select(User).where(User.email == email))
    if user is not None:
        return user, None

    password = password_factory()
    user = User(email=email, password_hash=hash_password(password))
    session.add(user)
    session.flush()
    return user, password


def _membership(
    session: Session,
    *,
    user: User,
    organization: Organization,
    role: Role,
) -> Membership:
    membership = session.scalar(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.organization_id == organization.id,
        )
    )
    if membership is None:
        membership = Membership(
            user_id=user.id,
            organization_id=organization.id,
            role=role,
        )
        session.add(membership)
        session.flush()
    return membership


def _group(session: Session, organization: Organization, name: str) -> Group:
    group = session.scalar(
        select(Group).where(
            Group.organization_id == organization.id,
            Group.name == name,
        )
    )
    if group is None:
        group = Group(organization_id=organization.id, name=name)
        session.add(group)
        session.flush()
    return group


def seed_demo(
    session: Session,
    *,
    emit_credentials: bool = False,
    password_factory: Callable[[], str] | None = None,
) -> DemoManifest:
    create_password = password_factory or (lambda: secrets.token_urlsafe(18))
    organizations = {
        slug: _organization(session, slug=slug, name=name)
        for slug, name, _groups in _ORGANIZATIONS
    }

    users: dict[str, User] = {}
    for spec in _NORTHSTAR_USERS:
        user, password = _user(session, spec.email, create_password)
        users[spec.email] = user
        if emit_credentials and password is not None:
            print(f"{spec.email} password={password}")
        _membership(
            session,
            user=user,
            organization=organizations["northstar-labs"],
            role=spec.role,
        )

    _membership(
        session,
        user=users["owner@northstar.example"],
        organization=organizations["harbor-works"],
        role=Role.MEMBER,
    )
    for slug, _name, group_names in _ORGANIZATIONS:
        for group_name in group_names:
            _group(session, organizations[slug], group_name)

    return DemoManifest(
        organization_slugs=tuple(sorted(organizations)),
        user_count=len(users),
        membership_count=5,
        group_count=sum(len(group_names) for _, _, group_names in _ORGANIZATIONS),
    )


def run_demo_seed(settings: Settings) -> DemoManifest:
    if settings.environment == "production":
        raise RuntimeError("demo seeding is disabled in production")

    engine = build_engine(settings.database_admin_url)
    try:
        with Session(engine) as session:
            manifest = seed_demo(session, emit_credentials=True)
            session.commit()
            return manifest
    finally:
        engine.dispose()


def main() -> None:
    run_demo_seed(Settings())  # type: ignore[call-arg]


if __name__ == "__main__":
    main()
