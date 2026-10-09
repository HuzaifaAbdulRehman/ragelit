import json
import re
import subprocess
from pathlib import Path
from typing import Annotated, Self
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from sqlalchemy import select, text

from app.audits.contracts import AuditModel, Identifier
from app.audits.isolation_reports import IsolationStrategy
from app.audits.workspace import AuditWorkspace
from app.documents.models import DocumentVersion
from app.evaluation.dataset import generate_utility_corpus
from app.evaluation.reports import _collections

_ERROR = "utility_cost_invalid"
CollectionName = Annotated[str, Field(max_length=100)]


class IngestionTiming(AuditModel):
    document_id: Identifier
    duration_ms: float = Field(strict=True, ge=0)
    ready: bool = Field(strict=True)


class IndexBuildMeasurement(AuditModel):
    timings: tuple[IngestionTiming, ...] = Field(max_length=87)
    reused: bool = Field(default=False, strict=True)
    runtime_failed: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def validate_inventory(self) -> Self:
        expected = tuple(doc.id for doc in generate_utility_corpus().documents)
        if (
            tuple(event.document_id for event in self.timings)
            != expected[: len(self.timings)]
            or self.reused
            and self.timings
        ):
            raise ValueError(_ERROR)
        return self

    @property
    def expected_documents(self) -> int:
        return 87

    @property
    def recorded_documents(self) -> int:
        return len(self.timings)

    @property
    def coverage_complete(self) -> bool:
        return (
            not self.reused
            and not self.runtime_failed
            and len(self.timings) == 87
            and all(event.ready for event in self.timings)
        )

    @property
    def observed_ingestion_ms(self) -> float | None:
        return (
            sum(event.duration_ms for event in self.timings) if self.timings else None
        )

    @property
    def index_build_ms(self) -> float | None:
        return self.observed_ingestion_ms if self.coverage_complete else None


def index_build_measurement(
    timings: tuple[IngestionTiming, ...],
    *,
    reused: bool = False,
    runtime_failed: bool = False,
) -> IndexBuildMeasurement:
    return IndexBuildMeasurement.model_validate(
        {
            "timings": tuple(event.model_dump() for event in timings),
            "reused": reused,
            "runtime_failed": runtime_failed,
        }
    )


class CollectionDiskMeasurement(AuditModel):
    name: CollectionName
    allocated_kib: int = Field(strict=True, ge=0)


class StorageMeasurement(AuditModel):
    database_bytes: int = Field(strict=True, ge=1)
    upload_bytes: int = Field(strict=True, ge=0)
    collection_names: tuple[CollectionName, ...] = Field(min_length=1, max_length=3)
    disks: tuple[CollectionDiskMeasurement, ...] = Field(max_length=3)

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        strategy: IsolationStrategy = (
            "tenant_collections"
            if len(self.collection_names) == 3
            else "shared_pre_filter"
        )
        _collections(strategy, self.collection_names)
        names = tuple(item.name for item in self.disks)
        if len(set(names)) != len(names) or not set(names).issubset(
            self.collection_names
        ):
            raise ValueError(_ERROR)
        return self

    @property
    def collection_count(self) -> int:
        return len(self.collection_names)

    @property
    def coverage_complete(self) -> bool:
        return set(item.name for item in self.disks) == set(self.collection_names)

    @property
    def qdrant_collection_disk_bytes(self) -> int | None:
        return (
            sum(item.allocated_kib * 1024 for item in self.disks)
            if self.coverage_complete
            else None
        )


def storage_measurement(
    *,
    database_bytes: int,
    upload_bytes: int,
    collection_names: tuple[str, ...],
    disks: tuple[CollectionDiskMeasurement, ...],
) -> StorageMeasurement:
    return StorageMeasurement.model_validate(
        {
            "database_bytes": database_bytes,
            "upload_bytes": upload_bytes,
            "collection_names": collection_names,
            "disks": tuple(item.model_dump() for item in disks),
        }
    )


def read_collection_disks(
    workspace: AuditWorkspace, *, container: str
) -> tuple[CollectionDiskMeasurement, ...]:
    workspace.validate_owned()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", container):
        raise ValueError(_ERROR)
    names = workspace.store.collection_names()
    _collections(workspace.config.vector_strategy, names)
    endpoint = urlsplit(workspace.config.qdrant_url)
    identity = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            '{"id":{{json .Id}},"image":{{json .Config.Image}},'
            '"ports":{{json .NetworkSettings.Ports}}}',
            container,
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout
    if len(identity) > 65536:
        raise ValueError(_ERROR)
    info = json.loads(identity)
    version = workspace.store.client.info().version
    expected_images = {f"qdrant/qdrant:v{version}"}
    if version == "1.19.2":
        expected_images.add("ragelit-qdrant:v1.19.2-pcre2-10.46-deb13u3")
    if (
        not re.fullmatch(r"[0-9a-f]{64}", info["id"])
        or info["image"] not in expected_images
        or not any(
            binding["HostIp"] == endpoint.hostname
            and binding["HostPort"] == str(endpoint.port)
            for binding in info["ports"].get("6333/tcp", ()) or ()
        )
    ):
        raise ValueError(_ERROR)
    measured = []
    for name in names:
        directory = f"/qdrant/storage/collections/{name}"
        output = subprocess.run(
            ["docker", "exec", info["id"], "du", "-sk", "--", directory],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout
        if len(output) > 1024:
            raise ValueError(_ERROR)
        parts = output.strip().split(maxsplit=1)
        if (
            len(parts) != 2
            or not re.fullmatch(r"[0-9]{1,18}", parts[0])
            or parts[1] != directory
        ):
            raise ValueError(_ERROR)
        measured.append(
            CollectionDiskMeasurement(name=name, allocated_kib=int(parts[0]))
        )
    workspace.validate_owned()
    return tuple(measured)


def collect_storage(
    workspace: AuditWorkspace, *, qdrant_container: str | None = None
) -> StorageMeasurement:
    workspace.validate_owned()
    with workspace.admin_engine.connect() as connection:
        database_bytes: int = connection.execute(
            text("SELECT pg_database_size(current_database())")
        ).scalar_one()
        keys = tuple(connection.execute(select(DocumentVersion.storage_key)).scalars())
    uploads = tuple(workspace.config.data_dir / key for key in keys)
    if any(
        path.is_symlink()
        or path.is_junction()
        or not path.resolve().is_relative_to(workspace.config.data_dir.resolve())
        for path in uploads
    ):
        raise ValueError(_ERROR)
    upload_bytes = sum(Path(path).stat().st_size for path in uploads)
    disks = (
        read_collection_disks(workspace, container=qdrant_container)
        if qdrant_container is not None
        else ()
    )
    workspace.validate_owned()
    return storage_measurement(
        database_bytes=database_bytes,
        upload_bytes=upload_bytes,
        collection_names=workspace.store.collection_names(),
        disks=disks,
    )
