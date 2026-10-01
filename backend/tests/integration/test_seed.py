import re
from collections.abc import Iterator
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import verify_password
from app.identity.models import User
from app.seed import run_demo_seed, seed_demo
from app.tenancy.enums import Role
from app.tenancy.models import Group, Membership, Organization


@pytest.fixture(scope="module")
def seed_database_engine() -> Iterator[Engine]:
    database_name = f"ragelit_test_{uuid4().hex}"
    assert re.fullmatch(r"ragelit_test_[0-9a-f]{32}", database_name)
    server_url = (
        "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/postgres"
        "?connect_timeout=5"
    )
    server_engine = create_engine(server_url, isolation_level="AUTOCOMMIT")
    with server_engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database_name}"'))

    database_url = (
        f"postgresql+psycopg://postgres:postgres@127.0.0.1:5432/{database_name}"
        "?connect_timeout=5"
    )
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine = create_engine(database_url)

    try:
        yield engine
    finally:
        engine.dispose()
        with server_engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{database_name}" WITH (FORCE)'))
        server_engine.dispose()


def test_seed_demo_is_idempotent_and_emits_new_credentials_once(
    seed_database_engine: Engine,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with Session(seed_database_engine, expire_on_commit=False) as session:
        first = seed_demo(
            session,
            emit_credentials=True,
            password_factory=lambda: "task-nine-test-password",
        )
        session.commit()
    first_output = capsys.readouterr()

    with Session(seed_database_engine, expire_on_commit=False) as session:
        second = seed_demo(session, emit_credentials=True)
        session.commit()
    second_output = capsys.readouterr()

    assert first == second
    assert first.organization_slugs == ("harbor-works", "northstar-labs")
    assert first.user_count == 4
    assert first.membership_count == 5
    assert first.group_count == 4
    assert first_output.err == ""
    assert second_output.out == ""
    assert second_output.err == ""

    credentials = {}
    for line in first_output.out.splitlines():
        email, password = line.split(" password=", maxsplit=1)
        credentials[email] = password
    assert set(credentials) == {
        "admin@northstar.example",
        "auditor@northstar.example",
        "member@northstar.example",
        "owner@northstar.example",
    }
    assert set(credentials.values()) == {"task-nine-test-password"}

    with Session(seed_database_engine) as session:
        organizations = list(session.scalars(select(Organization)))
        users = list(session.scalars(select(User)))
        memberships = list(session.scalars(select(Membership)))
        groups = list(session.scalars(select(Group)))

    assert {organization.slug for organization in organizations} == {
        "harbor-works",
        "northstar-labs",
    }
    northstar_id = next(
        organization.id
        for organization in organizations
        if organization.slug == "northstar-labs"
    )
    harbor_id = next(
        organization.id
        for organization in organizations
        if organization.slug == "harbor-works"
    )
    user_by_id = {user.id: user for user in users}
    northstar_roles = {
        user_by_id[membership.user_id].email: membership.role
        for membership in memberships
        if membership.organization_id == northstar_id
    }
    assert northstar_roles == {
        "admin@northstar.example": Role.ADMIN,
        "auditor@northstar.example": Role.AUDITOR,
        "member@northstar.example": Role.MEMBER,
        "owner@northstar.example": Role.OWNER,
    }
    harbor_memberships = [
        membership
        for membership in memberships
        if membership.organization_id == harbor_id
    ]
    assert len(harbor_memberships) == 1
    assert user_by_id[harbor_memberships[0].user_id].email == (
        "owner@northstar.example"
    )
    assert harbor_memberships[0].role is Role.MEMBER
    assert len({(group.organization_id, group.name) for group in groups}) == 4
    for user in users:
        password = credentials[user.email]
        assert user.password_hash != password
        assert verify_password(password, user.password_hash)


def test_seed_demo_is_silent_without_explicit_demo_output(
    seed_database_engine: Engine,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with Session(seed_database_engine) as session:
        seed_demo(session)
        session.commit()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_seed_command_refuses_production() -> None:
    settings = Settings.model_validate(
        {
            "environment": "production",
            "secret_key": "production-secret-that-is-at-least-32-bytes",
            "database_admin_url": "not-used",
            "database_url": "not-used",
            "qdrant_url": "http://qdrant:6333",
            "cookie_secure": True,
        }
    )

    with pytest.raises(RuntimeError, match="production"):
        run_demo_seed(settings)
