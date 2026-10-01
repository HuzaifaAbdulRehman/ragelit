import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, delete, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.security import hash_password, issue_access_token
from app.identity.models import RefreshSession, User
from app.identity.schemas import LoginCommand
from app.identity.service import login, logout_refresh, refresh
from app.tenancy.enums import Role
from app.tenancy.models import Group, Membership, Organization
from app.tenancy.rls import set_request_context
from app.tenancy.scope import load_current_principal


@dataclass(frozen=True, slots=True)
class Seed:
    user_a_id: UUID
    user_a_email: str
    user_b_id: UUID
    organization_a_id: UUID
    organization_a_slug: str
    organization_b_id: UUID
    group_a_id: UUID
    group_b_id: UUID


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "environment": "test",
            "secret_key": "rls-test-secret-that-is-at-least-32-bytes",
            "database_admin_url": "unused",
            "database_url": "unused",
            "qdrant_url": "http://qdrant:6333",
        }
    )


def test_application_role_has_only_required_global_table_privileges(
    database_engines: tuple[Engine, Engine],
) -> None:
    admin_engine, _ = database_engines
    with admin_engine.connect() as connection:
        privileges = {
            tuple(row)
            for row in connection.execute(
                text(
                    """
                    SELECT table_name, privilege_type
                    FROM information_schema.role_table_grants
                    WHERE grantee = 'ragelit_app'
                      AND table_schema = 'public'
                      AND table_name IN ('users', 'organizations')
                    """
                )
            )
        }

    assert privileges == {
        ("organizations", "SELECT"),
        ("organizations", "UPDATE"),
        ("users", "SELECT"),
    }


@pytest.fixture(scope="module")
def database_engines() -> Iterator[tuple[Engine, Engine]]:
    database_name = f"ragelit_test_{uuid4().hex}"
    assert re.fullmatch(r"ragelit_test_[0-9a-f]{32}", database_name)
    server_url = (
        "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/postgres"
        "?connect_timeout=5"
    )
    server_engine = create_engine(server_url, isolation_level="AUTOCOMMIT")
    with server_engine.connect() as connection:
        connection.execute(
            text(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT FROM pg_catalog.pg_roles
                        WHERE rolname = 'ragelit_app'
                    ) THEN
                        CREATE ROLE ragelit_app NOLOGIN NOSUPERUSER NOCREATEDB
                            NOCREATEROLE NOINHERIT NOBYPASSRLS;
                    END IF;
                END
                $$
                """
            )
        )
        connection.execute(
            text(
                """
                ALTER ROLE ragelit_app LOGIN PASSWORD 'ragelit-test-password'
                    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS
                """
            )
        )
        connection.execute(text(f'CREATE DATABASE "{database_name}"'))

    database_url = (
        f"postgresql+psycopg://postgres:postgres@127.0.0.1:5432/{database_name}"
        "?connect_timeout=5"
    )
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    admin_engine = create_engine(database_url)
    application_url = (
        "postgresql+psycopg://ragelit_app:ragelit-test-password@127.0.0.1:5432/"
        f"{database_name}?connect_timeout=5"
    )
    application_engine = create_engine(
        application_url,
        pool_size=1,
        max_overflow=0,
    )

    try:
        yield admin_engine, application_engine
    finally:
        application_engine.dispose()
        admin_engine.dispose()
        with server_engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{database_name}" WITH (FORCE)'))
        server_engine.dispose()


@pytest.fixture
def seed(database_engines: tuple[Engine, Engine]) -> Seed:
    admin_engine, _ = database_engines
    with Session(admin_engine) as session:
        user_a_email = f"a-{uuid4().hex}@example.com"
        user_a = User(
            email=user_a_email,
            password_hash=hash_password("password"),
        )
        user_b = User(
            email=f"b-{uuid4().hex}@example.com",
            password_hash=hash_password("password"),
        )
        organization_a_slug = uuid4().hex
        organization_a = Organization(
            name="Organization A",
            slug=organization_a_slug,
        )
        organization_b = Organization(name="Organization B", slug=uuid4().hex)
        session.add_all([user_a, user_b, organization_a, organization_b])
        session.flush()
        session.add_all(
            [
                Membership(
                    user_id=user_a.id,
                    organization_id=organization_a.id,
                    role=Role.OWNER,
                ),
                Membership(
                    user_id=user_a.id,
                    organization_id=organization_b.id,
                    role=Role.MEMBER,
                ),
                Membership(
                    user_id=user_b.id,
                    organization_id=organization_a.id,
                    role=Role.MEMBER,
                ),
                Membership(
                    user_id=user_b.id,
                    organization_id=organization_b.id,
                    role=Role.MEMBER,
                    is_active=False,
                ),
            ]
        )
        group_a = Group(organization_id=organization_a.id, name="Group A")
        group_b = Group(organization_id=organization_b.id, name="Group B")
        session.add_all([group_a, group_b])
        session.commit()
        return Seed(
            user_a_id=user_a.id,
            user_a_email=user_a_email,
            user_b_id=user_b.id,
            organization_a_id=organization_a.id,
            organization_a_slug=organization_a_slug,
            organization_b_id=organization_b.id,
            group_a_id=group_a.id,
            group_b_id=group_b.id,
        )


@pytest.fixture
def application_factory(
    database_engines: tuple[Engine, Engine],
) -> sessionmaker[Session]:
    _, application_engine = database_engines
    return sessionmaker(bind=application_engine, expire_on_commit=False)


def test_runtime_role_is_restricted_and_context_fails_closed(
    application_factory: sessionmaker[Session],
    seed: Seed,
) -> None:
    with application_factory() as session:
        current_user = session.scalar(select(func.current_user()))
        role_flags = session.execute(
            text(
                """
                SELECT rolsuper, rolcreaterole, rolcreatedb, rolbypassrls
                FROM pg_catalog.pg_roles
                WHERE rolname = current_user
                """
            )
        ).one()
        groups = list(session.scalars(select(Group)))

    assert current_user == "ragelit_app"
    assert role_flags == (False, False, False, False)
    assert groups == []


def test_runtime_role_cannot_delete_global_identity_rows(
    application_factory: sessionmaker[Session],
    seed: Seed,
) -> None:
    with application_factory() as session:
        with pytest.raises(DBAPIError):
            session.execute(delete(User).where(User.id == seed.user_a_id))


def test_organization_context_returns_only_its_rows(
    application_factory: sessionmaker[Session],
    seed: Seed,
) -> None:
    with application_factory() as session, session.begin():
        set_request_context(
            session,
            user_id=seed.user_a_id,
            organization_id=seed.organization_a_id,
        )
        context_organization = session.scalar(
            select(func.current_setting("app.organization_id", True))
        )
        groups = list(session.execute(select(Group.id, Group.organization_id)))

    assert context_organization == str(seed.organization_a_id)
    assert groups == [(seed.group_a_id, seed.organization_a_id)]


def test_context_cannot_insert_a_row_for_another_organization(
    application_factory: sessionmaker[Session],
    seed: Seed,
) -> None:
    with application_factory() as session:
        set_request_context(
            session,
            user_id=seed.user_a_id,
            organization_id=seed.organization_a_id,
        )
        session.add(Group(organization_id=seed.organization_b_id, name="Blocked"))

        with pytest.raises(DBAPIError):
            session.flush()


def test_user_can_list_only_their_active_memberships_across_organizations(
    application_factory: sessionmaker[Session],
    seed: Seed,
) -> None:
    with application_factory() as session, session.begin():
        set_request_context(
            session,
            user_id=seed.user_a_id,
            organization_id=uuid4(),
        )
        memberships = list(
            session.execute(
                select(Membership.user_id, Membership.organization_id).order_by(
                    Membership.organization_id
                )
            )
        )

    assert memberships == sorted(
        [
            (seed.user_a_id, seed.organization_a_id),
            (seed.user_a_id, seed.organization_b_id),
        ],
        key=lambda row: row[1],
    )


def test_transaction_context_does_not_leak_through_the_pool(
    application_factory: sessionmaker[Session],
    seed: Seed,
) -> None:
    with application_factory() as session, session.begin():
        first_backend = session.scalar(select(func.pg_backend_pid()))
        set_request_context(
            session,
            user_id=seed.user_a_id,
            organization_id=seed.organization_a_id,
        )
        assert set(session.scalars(select(Group.id))) == {seed.group_a_id}
        set_request_context(
            session,
            user_id=seed.user_a_id,
            organization_id=seed.organization_b_id,
        )
        assert set(session.scalars(select(Group.id))) == {seed.group_b_id}

    with application_factory() as session:
        second_backend = session.scalar(select(func.pg_backend_pid()))
        assert list(session.scalars(select(Group.id))) == []

    assert second_backend == first_backend


def test_login_sets_context_before_reading_membership(
    application_factory: sessionmaker[Session],
    seed: Seed,
) -> None:
    with application_factory() as session:
        result = login(
            LoginCommand(
                email=seed.user_a_email,
                password="password",
                organization_slug=seed.organization_a_slug,
            ),
            session=session,
            settings=_settings(),
        )

    assert result.access_token


def test_principal_sets_context_before_reading_tenant_rows(
    database_engines: tuple[Engine, Engine],
    application_factory: sessionmaker[Session],
    seed: Seed,
) -> None:
    admin_engine, _ = database_engines
    now = datetime.now(UTC)
    with Session(admin_engine) as session:
        stored = RefreshSession(
            user_id=seed.user_a_id,
            organization_id=seed.organization_a_id,
            token_hash=uuid4().hex,
            expires_at=now + timedelta(days=1),
        )
        session.add(stored)
        session.commit()
        access_token = issue_access_token(
            seed.user_a_id,
            seed.organization_a_id,
            stored.id,
            secret_key=_settings().secret_key.get_secret_value(),
            issued_at=now,
            lifetime=timedelta(minutes=15),
        )

    with application_factory() as session:
        principal = load_current_principal(
            access_token,
            session=session,
            settings=_settings(),
            now=now,
        )

    assert principal.user_id == seed.user_a_id
    assert principal.organization_id == seed.organization_a_id


def test_refresh_resolves_only_the_presented_token_before_tenant_context(
    database_engines: tuple[Engine, Engine],
    application_factory: sessionmaker[Session],
    seed: Seed,
) -> None:
    with application_factory() as session:
        logged_in = login(
            LoginCommand(
                email=seed.user_a_email,
                password="password",
                organization_slug=seed.organization_a_slug,
            ),
            session=session,
            settings=_settings(),
        )

    with application_factory() as session:
        rotated = refresh(
            logged_in.refresh_token,
            session=session,
            settings=_settings(),
        )

    assert rotated.refresh_token != logged_in.refresh_token
    admin_engine, _ = database_engines
    with Session(admin_engine) as session:
        original = session.get(RefreshSession, logged_in.session_id)
        assert original is not None
        sessions = list(
            session.scalars(
                select(RefreshSession).where(
                    RefreshSession.family_id == original.family_id
                )
            )
        )
    assert len(sessions) == 2
    assert sum(item.used_at is not None for item in sessions) == 1


def test_logout_resolves_only_the_presented_token_before_tenant_context(
    database_engines: tuple[Engine, Engine],
    application_factory: sessionmaker[Session],
    seed: Seed,
) -> None:
    with application_factory() as session:
        logged_in = login(
            LoginCommand(
                email=seed.user_a_email,
                password="password",
                organization_slug=seed.organization_a_slug,
            ),
            session=session,
            settings=_settings(),
        )

    with application_factory() as session:
        logout_refresh(logged_in.refresh_token, session=session)

    admin_engine, _ = database_engines
    with Session(admin_engine) as session:
        stored = session.get(RefreshSession, logged_in.session_id)
        assert stored is not None
        assert stored.revoked_at is not None
