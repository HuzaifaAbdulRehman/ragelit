import re
from collections.abc import Callable, Iterator
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.security import hash_password
from app.identity.models import User
from app.main import create_app
from app.tenancy.enums import Role
from app.tenancy.models import Group, Membership, Organization
from tests.api.document_support import vector_store as vector_store
from tests.api.tenant_support import TenantApiSeed

_PASSWORD = "correct password"
_PASSWORD_HASH = hash_password(_PASSWORD)


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "environment": "test",
            "secret_key": "tenant-api-test-secret-that-is-at-least-32-bytes",
            "database_admin_url": "unused",
            "database_url": "unused",
            "qdrant_url": "http://qdrant:6333",
        }
    )


@pytest.fixture(scope="session")
def tenant_settings() -> Settings:
    return _settings()


@pytest.fixture(scope="session")
def tenant_database_engines() -> Iterator[tuple[Engine, Engine]]:
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

    admin_url = (
        f"postgresql+psycopg://postgres:postgres@127.0.0.1:5432/{database_name}"
        "?connect_timeout=5"
    )
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", admin_url)
    command.upgrade(config, "head")
    admin_engine = create_engine(admin_url)
    application_url = (
        "postgresql+psycopg://ragelit_app:ragelit-test-password@127.0.0.1:5432/"
        f"{database_name}?connect_timeout=5"
    )
    application_engine = create_engine(application_url)
    try:
        yield admin_engine, application_engine
    finally:
        application_engine.dispose()
        admin_engine.dispose()
        with server_engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{database_name}" WITH (FORCE)'))
        server_engine.dispose()


@pytest.fixture
def tenant_seed(tenant_database_engines: tuple[Engine, Engine]) -> TenantApiSeed:
    admin_engine, _ = tenant_database_engines
    suffix = uuid4().hex
    with Session(admin_engine, expire_on_commit=False) as session:
        owner = User(
            email=f"owner-{suffix}@example.com",
            password_hash=_PASSWORD_HASH,
        )
        admin = User(
            email=f"admin-{suffix}@example.com",
            password_hash=_PASSWORD_HASH,
        )
        auditor = User(
            email=f"auditor-{suffix}@example.com",
            password_hash=_PASSWORD_HASH,
        )
        member = User(
            email=f"member-{suffix}@example.com",
            password_hash=_PASSWORD_HASH,
        )
        outsider = User(
            email=f"outsider-{suffix}@example.com",
            password_hash=_PASSWORD_HASH,
        )
        organization_a = Organization(name=f"A {suffix}", slug=f"a-{suffix}")
        organization_b = Organization(name=f"B secret {suffix}", slug=f"b-{suffix}")
        organization_c = Organization(name=f"C secret {suffix}", slug=f"c-{suffix}")
        organization_d = Organization(name=f"D inactive {suffix}", slug=f"d-{suffix}")
        session.add_all(
            [
                owner,
                admin,
                auditor,
                member,
                outsider,
                organization_a,
                organization_b,
                organization_c,
                organization_d,
            ]
        )
        session.flush()
        owner_a = Membership(
            user_id=owner.id,
            organization_id=organization_a.id,
            role=Role.OWNER,
        )
        owner_b = Membership(
            user_id=owner.id,
            organization_id=organization_b.id,
            role=Role.MEMBER,
        )
        owner_d = Membership(
            user_id=owner.id,
            organization_id=organization_d.id,
            role=Role.MEMBER,
            is_active=False,
        )
        admin_a = Membership(
            user_id=admin.id,
            organization_id=organization_a.id,
            role=Role.ADMIN,
        )
        auditor_a = Membership(
            user_id=auditor.id,
            organization_id=organization_a.id,
            role=Role.AUDITOR,
        )
        member_a = Membership(
            user_id=member.id,
            organization_id=organization_a.id,
            role=Role.MEMBER,
        )
        outsider_b = Membership(
            user_id=outsider.id,
            organization_id=organization_b.id,
            role=Role.OWNER,
        )
        outsider_c = Membership(
            user_id=outsider.id,
            organization_id=organization_c.id,
            role=Role.OWNER,
        )
        outsider_d = Membership(
            user_id=outsider.id,
            organization_id=organization_d.id,
            role=Role.OWNER,
        )
        session.add_all(
            [
                owner_a,
                owner_b,
                owner_d,
                admin_a,
                auditor_a,
                member_a,
                outsider_b,
                outsider_c,
                outsider_d,
            ]
        )
        group_a = Group(
            organization_id=organization_a.id,
            name=f"A group {suffix}",
        )
        group_b = Group(
            organization_id=organization_b.id,
            name=f"B confidential group {suffix}",
        )
        session.add_all([group_a, group_b])
        session.commit()
        return TenantApiSeed(
            organization_a_id=organization_a.id,
            organization_a_name=organization_a.name,
            organization_a_slug=organization_a.slug,
            organization_b_id=organization_b.id,
            organization_b_name=organization_b.name,
            organization_b_slug=organization_b.slug,
            organization_c_id=organization_c.id,
            organization_c_name=organization_c.name,
            organization_d_id=organization_d.id,
            organization_d_name=organization_d.name,
            owner_email=owner.email,
            owner_membership_a_id=owner_a.id,
            owner_membership_b_id=owner_b.id,
            owner_membership_d_id=owner_d.id,
            admin_email=admin.email,
            admin_membership_id=admin_a.id,
            auditor_email=auditor.email,
            member_email=member.email,
            member_membership_id=member_a.id,
            outsider_email=outsider.email,
            outsider_membership_b_id=outsider_b.id,
            group_a_id=group_a.id,
            group_a_name=group_a.name,
            group_b_id=group_b.id,
            group_b_name=group_b.name,
        )


@pytest.fixture
def tenant_client(
    tenant_database_engines: tuple[Engine, Engine],
    tenant_settings: Settings,
) -> Iterator[TestClient]:
    _, application_engine = tenant_database_engines
    factory = sessionmaker(bind=application_engine, expire_on_commit=False)
    app = create_app(tenant_settings, session_factory=factory)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def login_headers(
    tenant_client: TestClient,
) -> Callable[[str, str], dict[str, str]]:
    def login_as(email: str, organization_slug: str) -> dict[str, str]:
        response = tenant_client.post(
            "/api/v1/auth/login",
            json={
                "email": email,
                "password": _PASSWORD,
                "organization_slug": organization_slug,
            },
        )
        assert response.status_code == 200
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return login_as
