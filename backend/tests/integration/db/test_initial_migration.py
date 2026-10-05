import re
from collections.abc import Iterator
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


@pytest.fixture
def empty_database_url() -> Iterator[str]:
    database_name = f"ragelit_test_{uuid4().hex}"
    assert re.fullmatch(r"ragelit_test_[0-9a-f]{32}", database_name)
    server_url = (
        "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/postgres"
        "?connect_timeout=5"
    )
    server_engine = create_engine(server_url, isolation_level="AUTOCOMMIT")

    with server_engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database_name}"'))

    try:
        yield (
            f"postgresql+psycopg://postgres:postgres@127.0.0.1:5432/{database_name}"
            "?connect_timeout=5"
        )
    finally:
        with server_engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{database_name}" WITH (FORCE)'))
        server_engine.dispose()


def test_initial_migration_round_trip(empty_database_url: str) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", empty_database_url)

    command.upgrade(config, "head")
    engine = create_engine(empty_database_url)
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) == {
        "alembic_version",
        "audit_runs",
        "group_members",
        "groups",
        "memberships",
        "organizations",
        "refresh_sessions",
        "users",
        "documents",
        "document_versions",
        "document_grants",
        "ingestion_jobs",
        "query_runs",
        "query_trace_stages",
    }
    assert {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("memberships")
    } == {"uq_memberships_user_organization"}
    assert {
        constraint["name"] for constraint in inspector.get_unique_constraints("groups")
    } == {
        "uq_groups_id_organization",
        "uq_groups_organization_name",
    }
    assert {
        constraint["name"] for constraint in inspector.get_foreign_keys("memberships")
    } == {
        "fk_memberships_organization_id_organizations",
        "fk_memberships_user_id_users",
    }
    assert {
        constraint["name"] for constraint in inspector.get_foreign_keys("group_members")
    } == {
        "fk_group_members_group_organization",
        "fk_group_members_membership",
    }
    assert {
        constraint["name"]
        for constraint in inspector.get_foreign_keys("refresh_sessions")
    } == {
        "fk_refresh_sessions_membership",
        "fk_refresh_sessions_replaced_by_id_refresh_sessions",
    }
    engine.dispose()

    command.downgrade(config, "base")
    engine = create_engine(empty_database_url)
    assert inspect(engine).get_table_names() == ["alembic_version"]
    engine.dispose()

    command.upgrade(config, "head")
