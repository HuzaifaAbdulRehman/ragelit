import re
from collections.abc import Iterator
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.security import hash_password
from app.identity.models import RefreshSession, User
from app.main import create_app
from app.tenancy.enums import Role
from app.tenancy.models import Membership, Organization


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "environment": "test",
            "secret_key": "api-test-secret-that-is-at-least-32-bytes",
            "database_admin_url": "unused",
            "database_url": "unused",
            "qdrant_url": "http://qdrant:6333",
            "cookie_secure": False,
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
    session = factory()
    user = User(
        email="person@example.com",
        password_hash=hash_password("correct password"),
    )
    organization = Organization(name="Example", slug="example")
    session.add_all([user, organization])
    session.flush()
    session.add(
        Membership(
            user_id=user.id,
            organization_id=organization.id,
            role=Role.MEMBER,
        )
    )
    session.commit()
    session.close()
    try:
        yield factory
    finally:
        transaction.rollback()
        connection.close()


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    app = create_app(
        _settings(),
        {"postgres": lambda: True, "qdrant": lambda: True},
        session_factory=session_factory,
    )
    with TestClient(app) as test_client:
        yield test_client


def _login(client: TestClient) -> Response:
    return client.post(
        "/api/v1/auth/login",
        json={
            "email": "person@example.com",
            "password": "correct password",
            "organization_slug": "example",
        },
    )


def test_login_returns_access_token_and_http_only_refresh_cookie(
    client: TestClient,
) -> None:
    response = _login(client)

    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"
    assert "access_token" in response.json()
    assert "refresh_token" not in response.json()
    assert response.headers["cache-control"] == "no-store"
    cookie = response.headers["set-cookie"].lower()
    assert "ragelit_refresh=" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


@pytest.mark.parametrize("email", ["missing@example.com", "person@example.com"])
def test_login_failure_does_not_enumerate_accounts(
    client: TestClient,
    email: str,
) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "wrong password",
            "organization_slug": "example",
        },
    )

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"
    assert response.json()["detail"] == "Authentication failed."


def test_refresh_uses_cookie_and_rotates_it(client: TestClient) -> None:
    login_response = _login(client)
    original_cookie = login_response.cookies["ragelit_refresh"]

    response = client.post("/api/v1/auth/refresh")

    assert response.status_code == 200
    assert response.cookies["ragelit_refresh"] != original_cookie
    assert "refresh_token" not in response.json()
    assert response.headers["cache-control"] == "no-store"


def test_logout_revokes_session_and_clears_cookie(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    _login(client)

    response = client.post("/api/v1/auth/logout")

    assert response.status_code == 204
    cleared_cookie = response.headers["set-cookie"].lower()
    assert "ragelit_refresh=" in cleared_cookie
    assert "max-age=0" in cleared_cookie
    assert "path=/api/v1/auth" in cleared_cookie
    with session_factory() as session:
        stored = session.scalar(select(RefreshSession))
        assert stored is not None
        assert stored.revoked_at is not None
