import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from app.documents.extraction import DocumentError


@dataclass(frozen=True, slots=True)
class StoredUpload:
    path: Path
    storage_key: str
    checksum: str
    byte_count: int


def storage_path(root: Path, storage_key: str) -> Path:
    parts = storage_key.split("/")
    if len(parts) != 2 or not parts[1].endswith(".upload"):
        raise DocumentError("invalid_document")
    try:
        UUID(parts[0])
        UUID(parts[1][:-7])
    except ValueError as error:
        raise DocumentError("invalid_document") from error
    path = (root / storage_key).resolve()
    if not path.is_relative_to(root.resolve()):
        raise DocumentError("invalid_document")
    return path


async def stream_upload(
    body: AsyncIterator[bytes],
    *,
    root: Path,
    organization_id: UUID,
    version_id: UUID,
    max_bytes: int,
) -> StoredUpload:
    key = f"{organization_id}/{version_id}.upload"
    path = storage_path(root, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    complete, size = False, 0
    digest = hashlib.sha256()
    try:
        with path.open("xb") as stream:
            async for chunk in body:
                size += len(chunk)
                if size > max_bytes:
                    raise DocumentError("upload_too_large", 413)
                digest.update(chunk)
                stream.write(chunk)
        if size == 0:
            raise DocumentError("empty_document")
        complete = True
        return StoredUpload(path, key, digest.hexdigest(), size)
    finally:
        if not complete:
            path.unlink(missing_ok=True)
