import hashlib
import json
import stat
from importlib.metadata import version
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from app.audits.contracts import AuditModel, Checksum
from app.retrieval.embeddings import FastEmbedProvider

_DENSE_FILES = frozenset(
    {
        "README.md",
        "config.json",
        "ort_config.json",
        "special_tokens_map.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "vocab.txt",
        "model_optimized.onnx",
    }
)
_SPARSE_FILES = frozenset({"README.md", "config.json", "english.txt"})
_DENSE_OPTIONS = {"threads": 2, "providers": ["CPUExecutionProvider"], "cuda": False}
_SPARSE_OPTIONS = {
    "threads": 2,
    "k": 1.2,
    "b": 0.75,
    "avg_len": 256.0,
    "language": "english",
    "token_max_length": 40,
    "disable_stemmer": False,
}


class AssetPin(AuditModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,80}$")
    size: int = Field(ge=0, le=70 * 1024 * 1024)
    sha256: Checksum


class ModelPin(AuditModel):
    model: str
    repository: str
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    license: Literal["mit", "apache-2.0"]
    files: tuple[AssetPin, ...] = Field(min_length=1, max_length=16)


class EmbeddingPins(AuditModel):
    schema_version: Literal["embedding-1"]
    fastembed_version: Literal["0.8.1"]
    dense: ModelPin
    sparse: ModelPin

    @model_validator(mode="after")
    def fixed_model_inventory(self) -> Self:
        for pin, names, model, repository, license_id in (
            (
                self.dense,
                _DENSE_FILES,
                "BAAI/bge-small-en-v1.5",
                "Qdrant/bge-small-en-v1.5-onnx-Q",
                "mit",
            ),
            (self.sparse, _SPARSE_FILES, "Qdrant/bm25", "Qdrant/bm25", "apache-2.0"),
        ):
            if (
                len(pin.files) != len(names)
                or {asset.name for asset in pin.files} != names
                or pin.model != model
                or pin.repository != repository
                or pin.license != license_id
            ):
                raise ValueError("embedding_pin_inventory")
        return self

    @property
    def fingerprint(self) -> str:
        payload = self.model_dump(mode="json") | {
            "dimension": 384,
            "dense_settings": _DENSE_OPTIONS,
            "sparse_settings": _SPARSE_OPTIONS,
            "local_files_only": True,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


def load_embedding_pins() -> EmbeddingPins:
    with Path(__file__).with_name("embedding-pins.json").open("rb") as stream:
        raw = stream.read(16 * 1024 + 1)
    if len(raw) > 16 * 1024:
        raise ValueError("embedding_pin_size")
    return EmbeddingPins.model_validate_json(raw)


def _guard_path(path: Path, *, directory: bool) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("embedding_asset_path")
    for component in (path, *path.parents):
        if component.is_symlink() or component.is_junction():
            raise ValueError("embedding_asset_path")
    if directory and not path.is_dir():
        raise ValueError("embedding_asset_path")
    if not directory and not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("embedding_asset_path")


def verify_embedding_assets(root: Path) -> EmbeddingPins:
    pins = load_embedding_pins()
    if version("fastembed") != pins.fastembed_version:
        raise ValueError("embedding_package_changed")
    _guard_path(root, directory=True)
    if {entry.name for entry in root.iterdir()} != {"dense", "sparse"}:
        raise ValueError("embedding_asset_inventory")
    for kind, pin in (("dense", pins.dense), ("sparse", pins.sparse)):
        folder = root / kind
        _guard_path(folder, directory=True)
        if {entry.name for entry in folder.iterdir()} != {
            asset.name for asset in pin.files
        }:
            raise ValueError("embedding_asset_inventory")
        for asset in pin.files:
            path = folder / asset.name
            _guard_path(path, directory=False)
            if path.stat().st_size != asset.size:
                raise ValueError("embedding_asset_changed")
            digest = hashlib.sha256()
            count = 0
            with path.open("rb") as stream:
                while chunk := stream.read(min(64 * 1024, asset.size - count + 1)):
                    count += len(chunk)
                    if count > asset.size:
                        raise ValueError("embedding_asset_changed")
                    digest.update(chunk)
            if count != asset.size or digest.hexdigest() != asset.sha256:
                raise ValueError("embedding_asset_changed")
    return pins


class PinnedEmbeddingProvider(FastEmbedProvider):
    def __init__(self, root: Path) -> None:
        self.pins = verify_embedding_assets(root)
        from fastembed import SparseTextEmbedding, TextEmbedding

        self._dense = TextEmbedding(
            self.pins.dense.model,
            cache_dir=str(root),
            specific_model_path=str(root / "dense"),
            local_files_only=True,
            threads=2,
            providers=["CPUExecutionProvider"],
            cuda=False,
        )
        self._sparse = SparseTextEmbedding(
            self.pins.sparse.model,
            cache_dir=str(root),
            specific_model_path=str(root / "sparse"),
            local_files_only=True,
            threads=2,
            k=1.2,
            b=0.75,
            avg_len=256.0,
            language="english",
            token_max_length=40,
            disable_stemmer=False,
        )

    @property
    def fingerprint(self) -> str:
        return self.pins.fingerprint
