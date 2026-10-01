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


def test_intended_version_migration_preserves_existing_rows_and_rls(
    migration_database_url: str,
) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", migration_database_url)
    command.upgrade(config, "0005_query_traces")
    organization_id, document_id, version_id = uuid4(), uuid4(), uuid4()
    engine = create_engine(migration_database_url)
    values = {
        "org": organization_id,
        "doc": document_id,
        "version": version_id,
        "checksum": uuid4().hex * 2,
    }
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO organizations (id, name, slug) "
                    "VALUES (:org, 'legacy', 'legacy')"
                ),
                values,
            )
            connection.execute(
                text("""INSERT INTO documents
                    (id, organization_id, filename, media_type, checksum, state)
                    VALUES (:doc, :org, 'legacy.txt', 'text/plain',
                            :checksum, 'ready')"""),
                values,
            )
            connection.execute(
                text("""INSERT INTO document_versions
                    (id, organization_id, document_id, storage_key,
                     checksum, byte_count, state)
                    VALUES (:version, :org, :doc, 'synthetic',
                            :checksum, 1, 'ready')"""),
                values,
            )
        for _ in range(2):
            command.upgrade(config, "head")
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text(
                            "SELECT current_version_id FROM documents WHERE id = :doc"
                        ),
                        values,
                    ).scalar_one()
                    == version_id
                )
                assert (
                    connection.execute(
                        text("""SELECT count(*) FROM pg_class
                        WHERE relname IN ('documents', 'document_versions')
                        AND relrowsecurity AND relforcerowsecurity""")
                    ).scalar_one()
                    == 2
                )
            command.downgrade(config, "0005_query_traces")
    finally:
        engine.dispose()
