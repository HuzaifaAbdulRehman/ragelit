from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from qdrant_client import QdrantClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.audits.workspace import AuditConfiguration


@pytest.fixture
def audit_config(tmp_path: Path) -> Iterator[AuditConfiguration]:
    name = f"ragelit_audit_{uuid4().hex}"
    config = AuditConfiguration.model_validate(
        {
            "environment": "test",
            "database_admin_url": f"postgresql+psycopg://postgres:postgres@127.0.0.1:5432/{name}?connect_timeout=5",
            "qdrant_url": "http://127.0.0.1:6333",
            "root": tmp_path / name,
            "application_password": "audit-test-application-password",
            "fixture_password": "AuditFixturePasswordMarker",
        }
    )
    yield config
    config.validate_paths()
    assert config.name == name and name.startswith("ragelit_audit_")
    engine = create_engine(
        make_url(config.database_admin_url.get_secret_value()).set(database="postgres"),
        isolation_level="AUTOCOMMIT",
    )
    with engine.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        connection.execute(text(f'DROP ROLE IF EXISTS "{config.application_role}"'))
    engine.dispose()
    client = QdrantClient(
        url=config.qdrant_url, timeout=config.qdrant_timeout_seconds, trust_env=False
    )
    try:
        if client.collection_exists(name):
            client.delete_collection(name)
    finally:
        client.close()
