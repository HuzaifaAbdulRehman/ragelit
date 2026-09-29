import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import build_access_scope, get_current_principal
from app.core.config import Settings
from app.core.security import hash_password, issue_access_token
from app.identity.models import RefreshSession, User
from app.identity.schemas import LoginCommand
from app.identity.service import login
from app.main import create_app
from app.tenancy.enums import Role
from app.tenancy.models import Group, GroupMember, Membership, Organization
from app.tenancy.scope import AccessScope, RequestPrincipal


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "environment": "test",
            "secret_key": "principal-test-secret-that-is-at-least-32-bytes",
            "database_admin_url": "unused",
            "database_url": "unused",
            "qdrant_url": "http://qdrant:6333",
        }
    )


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
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


@pytest.fixture
def session_factory(database_engine: Engine) -> Iterator[sessionmaker[Session]]:
    connection = database_engine.connect()
    transaction = connection.begin()
    factory = sessionmaker(bind=connection, expire_on_commit=False)
    try:
        yield factory
    finally:
        transaction.rollback()
        connection.close()


@pytest.fixture
def identity(
    session_factory: sessionmaker[Session],
) -> tuple[User, Organization, Membership, Group]:
    with session_factory() as session:
        user = User(
            email="person@example.com",
            password_hash=hash_password("correct password"),
        )
        organization = Organization(name="Example", slug="example")
        session.add_all([user, organization])
        session.flush()
        membership = Membership(
            user_id=user.id,
            organization_id=organization.id,
            role=Role.MEMBER,
        )
        group = Group(organization_id=organization.id, name="Readers")
        session.add_all([membership, group])
        session.flush()
        session.add(
            GroupMember(
                organization_id=organization.id,
                group_id=group.id,
                user_id=user.id,
            )
        )
        session.commit()
        return user, organization, membership, group


@pytest.fixture
def access_token(
    session_factory: sessionmaker[Session],
    identity: tuple[User, Organization, Membership, Group],
) -> str:
    with session_factory() as session:
        result = login(
            LoginCommand(
                email="person@example.com",
                password="correct password",
                organization_slug="example",
            ),
            session=session,
            settings=_settings(),
        )
        return result.access_token


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    app = create_app(_settings(), session_factory=session_factory)

    @app.get("/principal")
    def principal_route(
        principal: Annotated[RequestPrincipal, Depends(get_current_principal)],
    ) -> RequestPrincipal:
        return principal

    @app.get("/scope")
    def scope_route(
        scope: Annotated[AccessScope, Depends(build_access_scope)],
    ) -> AccessScope:
        return scope

    with TestClient(app) as test_client:
        yield test_client


def _headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "X-Organization-ID": str(uuid4()),
        "X-Role": Role.OWNER,
    }


def test_principal_and_scope_come_from_current_database_state(
    client: TestClient,
    access_token: str,
    identity: tuple[User, Organization, Membership, Group],
) -> None:
    user, organization, membership, group = identity

    principal_response = client.get("/principal", headers=_headers(access_token))
    scope_response = client.get("/scope", headers=_headers(access_token))

    assert principal_response.status_code == 200
    assert principal_response.json() == {
        "user_id": str(user.id),
        "organization_id": str(organization.id),
        "session_id": principal_response.json()["session_id"],
        "membership_id": str(membership.id),
        "role": Role.MEMBER,
    }
    assert scope_response.status_code == 200
    assert scope_response.json() == {
        "user_id": str(user.id),
        "organization_id": str(organization.id),
        "membership_id": str(membership.id),
        "role": Role.MEMBER,
        "group_ids": [str(group.id)],
    }


def test_changed_role_applies_without_a_new_access_token(
    client: TestClient,
    session_factory: sessionmaker[Session],
    access_token: str,
    identity: tuple[User, Organization, Membership, Group],
) -> None:
    _, _, membership, _ = identity
    with session_factory() as session:
        current = session.get(Membership, membership.id)
        assert current is not None
        current.role = Role.ADMIN
        session.commit()

    response = client.get("/principal", headers=_headers(access_token))

    assert response.status_code == 200
    assert response.json()["role"] == Role.ADMIN


def test_token_organization_must_match_the_current_session(
    client: TestClient,
    session_factory: sessionmaker[Session],
    identity: tuple[User, Organization, Membership, Group],
) -> None:
    user, _, _, _ = identity
    with session_factory() as session:
        stored = session.scalar(select(RefreshSession))
        assert stored is None
        stored = RefreshSession(
            user_id=user.id,
            organization_id=identity[1].id,
            token_hash=uuid4().hex,
            expires_at=datetime.max.replace(tzinfo=UTC),
        )
        session.add(stored)
        session.commit()
        forged = issue_access_token(
            user.id,
            uuid4(),
            stored.id,
            secret_key=_settings().secret_key.get_secret_value(),
            lifetime=timedelta(minutes=15),
        )

    response = client.get("/principal", headers=_headers(forged))

    assert response.status_code == 401
    assert response.json()["code"] == "authentication_failed"


@pytest.mark.parametrize("invalid_state", ["session", "user", "membership"])
def test_inactive_current_state_invalidates_an_existing_token(
    client: TestClient,
    session_factory: sessionmaker[Session],
    access_token: str,
    identity: tuple[User, Organization, Membership, Group],
    invalid_state: str,
) -> None:
    user, _, membership, _ = identity
    with session_factory() as session:
        if invalid_state == "session":
            stored = session.scalar(select(RefreshSession))
            assert stored is not None
            stored.revoked_at = datetime.now(UTC)
        elif invalid_state == "user":
            current_user = session.get(User, user.id)
            assert current_user is not None
            current_user.is_active = False
        else:
            current_membership = session.get(Membership, membership.id)
            assert current_membership is not None
            current_membership.is_active = False
        session.commit()

    response = client.get("/principal", headers=_headers(access_token))

    assert response.status_code == 401
    assert response.json()["code"] == "authentication_failed"


def test_missing_membership_invalidates_an_existing_token(
    client: TestClient,
    session_factory: sessionmaker[Session],
    access_token: str,
    identity: tuple[User, Organization, Membership, Group],
) -> None:
    _, _, membership, _ = identity
    with session_factory() as session:
        current = session.get(Membership, membership.id)
        assert current is not None
        session.delete(current)
        session.commit()

    response = client.get("/principal", headers=_headers(access_token))

    assert response.status_code == 401
    assert response.json()["code"] == "authentication_failed"
