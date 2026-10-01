import re
from collections.abc import Iterator
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from app.db.base import Base
from app.identity import models as identity_models
from app.tenancy import models as tenancy_models


@pytest.fixture
def migration_database_url() -> Iterator[str]:
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


def test_database_matches_single_migration_head(
    migration_database_url: str,
) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", migration_database_url)
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert len(heads) == 1

    command.upgrade(config, "head")
    engine = create_engine(migration_database_url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={"compare_type": True},
            )
            assert context.get_current_heads() == tuple(heads)
            assert compare_metadata(context, Base.metadata) == []
    finally:
        engine.dispose()


_ = identity_models, tenancy_models
