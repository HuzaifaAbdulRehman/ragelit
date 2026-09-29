import hashlib
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.security import decode_access_token, hash_password
from app.identity.models import RefreshSession, User
from app.identity.schemas import LoginCommand
from app.identity.service import AuthError, login, logout, refresh
from app.tenancy.enums import Role
from app.tenancy.models import Membership, Organization


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "environment": "test",
            "secret_key": "integration-secret-that-is-at-least-32-bytes",
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
def db(database_engine: Engine) -> Iterator[Session]:
    connection = database_engine.connect()
    transaction = connection.begin()
    factory = sessionmaker(bind=connection, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _seed_identity(
    db: Session,
    *,
    user_active: bool = True,
    membership_active: bool = True,
) -> tuple[User, Organization, Membership]:
    user = User(
        email="person@example.com",
        password_hash=hash_password("correct password"),
        is_active=user_active,
    )
    organization = Organization(name="Example", slug="example")
    db.add_all([user, organization])
    db.flush()
    membership = Membership(
        user_id=user.id,
        organization_id=organization.id,
        role=Role.MEMBER,
        is_active=membership_active,
    )
    db.add(membership)
    db.flush()
    return user, organization, membership


def test_login_stores_only_refresh_hash_and_issues_scoped_access_token(
    db: Session,
) -> None:
    user, organization, _ = _seed_identity(db)
    now = datetime.now(UTC)

    result = login(
        LoginCommand(
            email="PERSON@example.com",
            password="correct password",
            organization_slug="example",
        ),
        session=db,
        settings=_settings(),
        now=now,
    )

    stored = db.scalar(select(RefreshSession))
    assert stored is not None
    assert (
        stored.token_hash == hashlib.sha256(result.refresh_token.encode()).hexdigest()
    )
    assert result.refresh_token not in stored.token_hash
    assert stored.expires_at == now + timedelta(days=14)
    claims = decode_access_token(
        result.access_token,
        secret_key=_settings().secret_key.get_secret_value(),
    )
    assert claims["sub"] == str(user.id)
    assert claims["org"] == str(organization.id)
    assert claims["sid"] == str(stored.id)


@pytest.mark.parametrize(
    ("password", "user_active", "membership_active"),
    [
        ("wrong password", True, True),
        ("correct password", False, True),
        ("correct password", True, False),
    ],
)
def test_login_rejects_invalid_or_inactive_identity(
    db: Session,
    password: str,
    user_active: bool,
    membership_active: bool,
) -> None:
    _seed_identity(
        db,
        user_active=user_active,
        membership_active=membership_active,
    )

    with pytest.raises(AuthError) as error:
        login(
            LoginCommand(
                email="person@example.com",
                password=password,
                organization_slug="example",
            ),
            session=db,
            settings=_settings(),
        )

    assert error.value.code == "invalid_credentials"
    assert db.scalar(select(RefreshSession)) is None


def test_refresh_rotates_token_and_replay_revokes_family(db: Session) -> None:
    _seed_identity(db)
    settings = _settings()
    logged_in = login(
        LoginCommand(
            email="person@example.com",
            password="correct password",
            organization_slug="example",
        ),
        session=db,
        settings=settings,
    )

    rotated = refresh(logged_in.refresh_token, session=db, settings=settings)
    sessions = list(
        db.scalars(select(RefreshSession).order_by(RefreshSession.created_at))
    )
    assert len(sessions) == 2
    assert sessions[0].used_at is not None
    assert sessions[0].replaced_by_id == sessions[1].id
    assert sessions[0].family_id == sessions[1].family_id
    assert rotated.refresh_token != logged_in.refresh_token

    with pytest.raises(AuthError) as error:
        refresh(logged_in.refresh_token, session=db, settings=settings)

    assert error.value.code == "refresh_reused"
    assert all(item.revoked_at is not None for item in sessions)


def test_logout_revokes_session(db: Session) -> None:
    _seed_identity(db)
    settings = _settings()
    logged_in = login(
        LoginCommand(
            email="person@example.com",
            password="correct password",
            organization_slug="example",
        ),
        session=db,
        settings=settings,
    )
    claims = decode_access_token(
        logged_in.access_token,
        secret_key=settings.secret_key.get_secret_value(),
    )

    logout(UUID(claims["sid"]), session=db)

    stored = db.scalar(select(RefreshSession))
    assert stored is not None
    assert stored.revoked_at is not None
