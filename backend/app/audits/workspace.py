import hashlib
import ipaddress
import json
import os
import re
import secrets
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, Self, cast
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

import psycopg
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from psycopg import sql
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from qdrant_client import QdrantClient
from sqlalchemy import Connection, Engine, create_engine, inspect, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.audits.contracts import AuditModel, Checksum, Identifier
from app.audits.embeddings import FixtureEmbeddings
from app.audits.fixtures import FixtureTemplate
from app.core.config import Settings
from app.db.base import Base
from app.db.session import build_session_factory
from app.documents.models import DocumentVersion
from app.main import create_app
from app.retrieval.store import QdrantChunkStore

_NAME = re.compile(r"ragelit_audit_[a-z0-9_]{1,32}\Z")
_MARKER = "audit_workspace_owner"
_EXCLUDED = {"password_hash", "token_hash"}


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


class AuditWorkspaceError(Exception):
    pass


class _AuditRuntimeSettings(Settings):
    model_config = SettingsConfigDict(env_file=None)
    database_admin_url: str = Field(min_length=1, repr=False)
    database_url: str = Field(min_length=1, repr=False)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


def _loopback(host: str | None) -> bool:
    try:
        return host is not None and ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _guard_path(path: Path, root: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise AuditWorkspaceError("unsafe_audit_path")
    for component in (path, *path.parents):
        if component.is_symlink() or component.is_junction():
            raise AuditWorkspaceError("unsafe_audit_path")
    if not path.resolve().is_relative_to(root.resolve()):
        raise AuditWorkspaceError("unsafe_audit_path")


class AuditConfiguration(AuditModel):
    environment: Literal["local", "test"]
    database_admin_url: SecretStr = Field(repr=False)
    qdrant_url: str
    root: Path
    application_password: SecretStr = Field(repr=False, min_length=16)
    fixture_password: SecretStr = Field(repr=False, min_length=16)
    qdrant_timeout_seconds: int = Field(default=30, ge=1, le=30)

    @model_validator(mode="after")
    def guard_targets(self) -> Self:
        database = make_url(self.database_admin_url.get_secret_value())
        if (
            database.drivername != "postgresql+psycopg"
            or not _loopback(database.host)
            or database.database is None
            or not _NAME.fullmatch(database.database)
            or set(database.query) - {"connect_timeout"}
            or database.query.get("connect_timeout", "5")
            not in {"1", "2", "3", "4", "5"}
        ):
            raise AuditWorkspaceError("invalid_audit_database")
        endpoint = urlsplit(self.qdrant_url)
        if (
            endpoint.scheme != "http"
            or not _loopback(endpoint.hostname)
            or endpoint.username is not None
            or endpoint.password is not None
            or endpoint.path not in {"", "/"}
            or endpoint.query
            or endpoint.fragment
            or endpoint.port is None
        ):
            raise AuditWorkspaceError("invalid_audit_vector_target")
        if self.root.name != self.name:
            raise AuditWorkspaceError("unsafe_audit_path")
        self.validate_paths()
        return self

    @property
    def admin_url(self) -> URL:
        database = make_url(self.database_admin_url.get_secret_value())
        return database.set(
            port=database.port or 5432,
            query=dict(database.query)
            | {
                "hostaddr": cast(str, database.host),
                "connect_timeout": database.query.get("connect_timeout", "5"),
            },
        )

    @property
    def name(self) -> str:
        return cast(str, self.admin_url.database)

    @property
    def application_role(self) -> str:
        return f"{self.name}_app"

    @property
    def application_url(self) -> URL:
        return self.admin_url.set(
            username=self.application_role,
            password=self.application_password.get_secret_value(),
        )

    @property
    def config_hash(self) -> str:
        endpoint = urlsplit(self.qdrant_url)
        return _hash(
            {
                "environment": self.environment,
                "database": self.name,
                "database_host": self.admin_url.host,
                "database_port": self.admin_url.port or 5432,
                "qdrant_host": endpoint.hostname,
                "qdrant_port": endpoint.port,
                "qdrant_timeout_seconds": self.qdrant_timeout_seconds,
                "collection": self.name,
                "root": str(self.root.resolve()),
            }
        )

    @property
    def workspace_id(self) -> UUID:
        return uuid5(NAMESPACE_URL, self.config_hash)

    @property
    def bindings_path(self) -> Path:
        return self.root / "bindings.json"

    @property
    def data_dir(self) -> Path:
        return self.root / "uploads"

    @property
    def report_dir(self) -> Path:
        return self.root / "reports"

    def validate_paths(self) -> None:
        _guard_path(self.root, self.root)
        for path in (self.bindings_path, self.data_dir, self.report_dir):
            _guard_path(path, self.root)


class DocumentBinding(AuditModel):
    document_id: UUID
    version_id: UUID
    chunk_ids: tuple[UUID, ...]
    content_hash: Checksum


class InstanceBinding(AuditModel):
    state: Literal["incomplete", "complete", "skipped"] = "incomplete"
    actor_id: UUID | None = None
    membership_id: UUID | None = None
    group_id: UUID | None = None
    document: DocumentBinding | None = None
    previous: DocumentBinding | None = None


class FixtureBindings(AuditModel):
    schema_version: Literal["1"] = "1"
    workspace_id: UUID
    template_hash: Checksum
    collection: Identifier
    seeded: bool = False
    organizations: dict[Identifier, UUID] = Field(default_factory=dict)
    actors: dict[Identifier, UUID] = Field(default_factory=dict)
    memberships: dict[Identifier, UUID] = Field(default_factory=dict)
    groups: dict[Identifier, UUID] = Field(default_factory=dict)
    documents: dict[Identifier, DocumentBinding] = Field(default_factory=dict)
    instances: dict[str, InstanceBinding] = Field(default_factory=dict)
    rows: dict[str, dict[str, Checksum]] = Field(default_factory=dict)
    points: dict[str, Checksum] = Field(default_factory=dict)

    @property
    def checksum(self) -> str:
        return _hash(self.model_dump(mode="json"))


class AuditWorkspace:
    store: QdrantChunkStore
    settings: Settings
    client: TestClient

    def __init__(self, config: AuditConfiguration, template: FixtureTemplate) -> None:
        self.config, self.template = config, template
        self.embeddings = FixtureEmbeddings()
        self._server_engine: Engine | None = None
        self._lock: Connection | None = None
        self._admin_engine: Engine | None = None
        self._application_engine: Engine | None = None
        self._vector_client: QdrantClient | None = None
        self._client: TestClient | None = None
        self._mutation_active = False
        self.bindings = FixtureBindings(
            workspace_id=config.workspace_id,
            template_hash=template.checksum,
            collection=config.name,
        )

    @property
    def admin_engine(self) -> Engine:
        if self._admin_engine is None:
            raise AuditWorkspaceError("audit_workspace_not_open")
        return self._admin_engine

    def __enter__(self) -> Self:
        self.config.validate_paths()
        try:
            self._server_engine = create_engine(
                self.config.admin_url.set(database="postgres"),
                isolation_level="AUTOCOMMIT",
                hide_parameters=True,
            )
            self._lock = self._server_engine.connect()
            lock_key = int.from_bytes(
                hashlib.sha256(self.config.name.encode()).digest()[:8],
                "big",
                signed=True,
            )
            if not self._lock.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key}
            ).scalar_one():
                raise AuditWorkspaceError("audit_workspace_busy")
            self._vector_client = QdrantClient(
                url=self.config.qdrant_url,
                timeout=self.config.qdrant_timeout_seconds,
                trust_env=False,
            )
            exists = (
                self._lock.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :name"),
                    {"name": self.config.name},
                ).scalar_one_or_none()
                is not None
            )
            if not exists:
                self._bootstrap()
            else:
                self._admin_engine = create_engine(
                    self.config.admin_url, hide_parameters=True
                )
                if _MARKER not in inspect(self.admin_engine).get_table_names():
                    raise AuditWorkspaceError("unowned_audit_workspace")
                self._load()
            self.store = QdrantChunkStore(
                self._vector_client,
                self.config.name,
                dimension=FixtureEmbeddings.dimension,
            )
            self.validate_owned()
            self._application_engine = create_engine(
                self.config.application_url, hide_parameters=True
            )
            self.factory: sessionmaker[Session] = build_session_factory(
                self._application_engine
            )
            self.settings = _AuditRuntimeSettings(
                _env_file=None,
                environment=self.config.environment,
                secret_key=SecretStr(secrets.token_hex(32)),
                database_admin_url=self.config.admin_url.render_as_string(
                    hide_password=False
                ),
                database_url=self.config.application_url.render_as_string(
                    hide_password=False
                ),
                qdrant_url=self.config.qdrant_url,
                qdrant_collection=self.config.name,
                data_dir=self.config.data_dir,
                llm_base_url=None,
                llm_model="",
                llm_api_key=SecretStr(""),
                allowed_origins=[],
                cookie_secure=False,
            )
            app = create_app(
                self.settings, readiness_checks={}, session_factory=self.factory
            )
            app.state.chunk_store, app.state.embeddings = (
                self.store,
                FixtureEmbeddings(),
            )
            self._client = TestClient(app)
            self.client = self._client.__enter__()
            return self
        except BaseException:
            self._close()
            raise

    def _bootstrap(self) -> None:
        assert self._lock is not None and self._vector_client is not None
        if self._vector_client.collection_exists(self.config.name) or (
            self.config.root.exists() and any(self.config.root.iterdir())
        ):
            raise AuditWorkspaceError("unowned_audit_workspace")
        if (
            self._lock.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
                {"role": self.config.application_role},
            ).scalar_one_or_none()
            is not None
        ):
            raise AuditWorkspaceError("unowned_audit_role")
        self._lock.execute(text(f'CREATE DATABASE "{self.config.name}"'))
        self._admin_engine = create_engine(self.config.admin_url, hide_parameters=True)
        migration = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        migration.set_main_option(
            "script_location", str(Path(__file__).resolve().parents[1] / "alembic")
        )
        migration.set_main_option(
            "sqlalchemy.url",
            self.config.admin_url.render_as_string(hide_password=False).replace(
                "%", "%%"
            ),
        )
        command.upgrade(migration, "head")
        with self.admin_engine.begin() as connection:
            if (
                connection.execute(
                    text("SELECT 1 FROM pg_roles WHERE rolname = 'ragelit_app'")
                ).scalar_one_or_none()
                is not None
            ):
                connection.execute(
                    text("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM ragelit_app")
                )
            driver = cast(
                psycopg.Connection[Any], connection.connection.driver_connection
            )
            with driver.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER "
                        "NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS"
                    ).format(
                        sql.Identifier(self.config.application_role),
                        sql.Literal(
                            self.config.application_password.get_secret_value()
                        ),
                    )
                )
                role = sql.Identifier(self.config.application_role)
                cursor.execute(
                    sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role)
                )
                cursor.execute(sql.SQL("GRANT SELECT ON users TO {}").format(role))
                cursor.execute(
                    sql.SQL("GRANT SELECT, UPDATE ON organizations TO {}").format(role)
                )
                tables = [
                    name
                    for name in Base.metadata.tables
                    if name not in {"users", "organizations"}
                ]
                cursor.execute(
                    sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON {} TO {}").format(
                        sql.SQL(",").join(map(sql.Identifier, tables)), role
                    )
                )
            connection.execute(
                text(
                    f"CREATE TABLE {_MARKER} ("
                    "singleton boolean PRIMARY KEY CHECK (singleton), "
                    "workspace_id uuid NOT NULL, template_hash text NOT NULL, "
                    "collection_name text NOT NULL, application_role text NOT NULL, "
                    "binding_hash text NOT NULL)"
                )
            )
            connection.execute(
                text(
                    f"INSERT INTO {_MARKER} VALUES "
                    "(true, :id, :template, :collection, :role, :hash)"
                ),
                {
                    "id": self.config.workspace_id,
                    "template": self.template.checksum,
                    "collection": self.config.name,
                    "role": self.config.application_role,
                    "hash": self.bindings.checksum,
                },
            )
        self.config.root.mkdir(parents=True, exist_ok=True)
        self.store = QdrantChunkStore(
            self._vector_client, self.config.name, dimension=FixtureEmbeddings.dimension
        )
        self.store.ensure_collection()
        self._save_snapshot()

    def _load(self) -> None:
        if not self.config.bindings_path.is_file():
            raise AuditWorkspaceError("audit_marker_drift")
        try:
            if self.config.bindings_path.stat().st_size > 4 * 1024 * 1024:
                raise AuditWorkspaceError("audit_marker_drift")
            self.bindings = FixtureBindings.model_validate_json(
                self.config.bindings_path.read_bytes()
            )
        except ValueError as error:
            raise AuditWorkspaceError("audit_marker_drift") from error

    def _row_snapshot(self) -> dict[str, dict[str, str]]:
        schemas = set(inspect(self.admin_engine).get_schema_names())
        if schemas - {"public", "information_schema"}:
            raise AuditWorkspaceError("audit_resource_drift")
        expected = set(Base.metadata.tables) | {_MARKER, "alembic_version"}
        if set(inspect(self.admin_engine).get_table_names()) != expected:
            raise AuditWorkspaceError("audit_resource_drift")
        result: dict[str, dict[str, str]] = {}
        with self.admin_engine.connect() as connection:
            for name, table in Base.metadata.tables.items():
                columns = [
                    column for column in table.columns if column.name not in _EXCLUDED
                ]
                rows = (
                    connection.execute(select(*columns).limit(10001)).mappings().all()
                )
                if len(rows) > 10000:
                    raise AuditWorkspaceError("audit_resource_drift")
                result[name] = {str(row["id"]): _hash(dict(row)) for row in rows}
        return result

    def _point_snapshot(self) -> dict[str, str]:
        if not self.store.client.collection_exists(self.config.name):
            raise AuditWorkspaceError("audit_resource_drift")
        result: dict[str, str] = {}
        offset: int | str | UUID | None = None
        while True:
            points, offset = self.store.client.scroll(
                self.config.name,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=True,
            )
            for point in points:
                result[str(point.id)] = _hash(point.model_dump(mode="json"))
            if len(result) > 5000:
                raise AuditWorkspaceError("audit_resource_drift")
            if offset is None:
                return result

    def _validate_uploads(self) -> None:
        expected: dict[Path, str] = {}
        with self.admin_engine.connect() as connection:
            for storage_key, checksum in connection.execute(
                select(DocumentVersion.storage_key, DocumentVersion.checksum)
            ):
                path = self.config.data_dir / storage_key
                _guard_path(path, self.config.root)
                expected[path] = checksum
        actual = (
            {path for path in self.config.data_dir.rglob("*") if path.is_file()}
            if self.config.data_dir.exists()
            else set()
        )
        if actual != set(expected):
            raise AuditWorkspaceError("audit_resource_drift")
        for path, checksum in expected.items():
            if hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
                raise AuditWorkspaceError("audit_resource_drift")

    def validate_owned(self) -> None:
        self.config.validate_paths()
        if self._lock is None or self._lock.closed:
            raise AuditWorkspaceError("audit_workspace_not_open")
        with self.admin_engine.connect() as connection:
            markers = (
                connection.execute(text(f"SELECT * FROM {_MARKER}")).mappings().all()
            )
            if len(markers) != 1:
                raise AuditWorkspaceError("audit_marker_drift")
            marker = markers[0]
            if (
                marker["workspace_id"] != self.config.workspace_id
                or marker["template_hash"] != self.template.checksum
                or marker["collection_name"] != self.config.name
                or marker["application_role"] != self.config.application_role
                or marker["binding_hash"] != self.bindings.checksum
                or self.bindings.workspace_id != self.config.workspace_id
                or self.bindings.template_hash != self.template.checksum
                or self.bindings.collection != self.config.name
                or not self.config.bindings_path.is_file()
            ):
                raise AuditWorkspaceError("audit_marker_drift")
            saved = FixtureBindings.model_validate_json(
                self.config.bindings_path.read_bytes()
            )
            if saved.checksum != self.bindings.checksum:
                raise AuditWorkspaceError("audit_marker_drift")
            flags = connection.execute(
                text(
                    "SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole "
                    "FROM pg_roles WHERE rolname = :role"
                ),
                {"role": self.config.application_role},
            ).one_or_none()
            if flags != (False, False, False, False):
                raise AuditWorkspaceError("unsafe_audit_role")
            owns_or_inherits: bool = connection.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_class c JOIN pg_roles r "
                    "ON r.oid = c.relowner WHERE r.rolname = :role) "
                    "OR EXISTS (SELECT 1 FROM pg_database d JOIN pg_roles r "
                    "ON r.oid = d.datdba WHERE r.rolname = :role) "
                    "OR EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles r "
                    "ON r.oid = m.member WHERE r.rolname = :role)"
                ),
                {"role": self.config.application_role},
            ).scalar_one()
            if owns_or_inherits:
                raise AuditWorkspaceError("unsafe_audit_role")
            for name in Base.metadata.tables:
                if name in {"users", "organizations"}:
                    continue
                flags = connection.execute(
                    text(
                        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                        "WHERE relname = :name "
                        "AND relnamespace = 'public'::regnamespace"
                    ),
                    {"name": name},
                ).one_or_none()
                if flags != (True, True):
                    raise AuditWorkspaceError("unsafe_audit_rls")
        if (
            self._row_snapshot() != self.bindings.rows
            or self._point_snapshot() != self.bindings.points
        ):
            raise AuditWorkspaceError("audit_resource_drift")
        self._validate_uploads()

    def _save_snapshot(self) -> None:
        self.config.validate_paths()
        self.bindings = self.bindings.model_copy(
            update={"rows": self._row_snapshot(), "points": self._point_snapshot()}
        )
        self._validate_uploads()
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.config.root, prefix=".bindings-", delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write((self.bindings.model_dump_json() + "\n").encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            with self.admin_engine.begin() as connection:
                connection.execute(
                    text(f"UPDATE {_MARKER} SET binding_hash = :hash WHERE singleton"),
                    {"hash": self.bindings.checksum},
                )
            os.replace(temporary, self.config.bindings_path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @contextmanager
    def mutation(self) -> Iterator[None]:
        if self._mutation_active:
            raise AuditWorkspaceError("nested_audit_mutation")
        self.validate_owned()
        self._mutation_active = True
        try:
            yield
            self._save_snapshot()
        finally:
            self._mutation_active = False

    def begin_instance(self, run_id: UUID, case_id: str) -> str:
        key = f"{run_id}:{case_id}"
        with self.mutation():
            if key in self.bindings.instances:
                raise AuditWorkspaceError("audit_instance_exists")
            self.bindings = self.bindings.model_copy(
                update={"instances": self.bindings.instances | {key: InstanceBinding()}}
            )
        return key

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._close()

    def _close(self) -> None:
        try:
            if self._client is not None:
                self._client.__exit__(None, None, None)
        finally:
            if self._vector_client is not None:
                self._vector_client.close()
            if self._application_engine is not None:
                self._application_engine.dispose()
            if self._admin_engine is not None:
                self._admin_engine.dispose()
            if self._lock is not None:
                self._lock.close()
            if self._server_engine is not None:
                self._server_engine.dispose()
