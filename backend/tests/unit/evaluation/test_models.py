import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from app.evaluation import models
from app.evaluation.models import (
    AssetPin,
    EmbeddingPins,
    PinnedEmbeddingProvider,
    load_embedding_pins,
    verify_embedding_assets,
)


@pytest.fixture
def assets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    pin_path = Path(__file__).parents[3] / "app/evaluation/embedding-pins.json"
    pins = EmbeddingPins.model_validate_json(pin_path.read_bytes())
    root = tmp_path / "models"
    root.mkdir()
    updates = {}
    for kind in ("dense", "sparse"):
        folder = root / kind
        folder.mkdir()
        model = getattr(pins, kind)
        files = []
        for asset in model.files:
            content = f"{kind}/{asset.name}".encode()
            (folder / asset.name).write_bytes(content)
            files.append(
                AssetPin(
                    name=asset.name,
                    size=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),
                )
            )
        updates[kind] = model.model_copy(update={"files": tuple(files)})
    tiny = pins.model_copy(update=updates)
    monkeypatch.setattr(models, "load_embedding_pins", lambda: tiny)
    return root


def test_valid_assets_bind_files_revisions_and_settings(assets: Path) -> None:
    pins = verify_embedding_assets(assets)
    assert len(pins.fingerprint) == 64
    assert pins.fingerprint == verify_embedding_assets(assets).fingerprint
    changed = pins.model_copy(
        update={"dense": pins.dense.model_copy(update={"revision": "f" * 40})}
    )
    assert pins.fingerprint != changed.fingerprint
    changed_file = pins.dense.files[0].model_copy(update={"sha256": "f" * 64})
    changed = pins.model_copy(
        update={
            "dense": pins.dense.model_copy(
                update={"files": (changed_file, *pins.dense.files[1:])}
            )
        }
    )
    assert pins.fingerprint != changed.fingerprint


@pytest.mark.parametrize(
    "relative",
    [
        "dense/README.md",
        "dense/config.json",
        "dense/ort_config.json",
        "dense/special_tokens_map.json",
        "dense/tokenizer.json",
        "dense/tokenizer_config.json",
        "dense/vocab.txt",
        "dense/model_optimized.onnx",
        "sparse/README.md",
        "sparse/config.json",
        "sparse/english.txt",
    ],
)
def test_changed_model_assets_fail_verification(assets: Path, relative: str) -> None:
    path = assets / relative
    content = path.read_bytes()
    path.write_bytes(bytes([content[0] ^ 1]) + content[1:])
    with pytest.raises(ValueError, match="embedding_asset_changed"):
        verify_embedding_assets(assets)


@pytest.mark.parametrize(
    "relative",
    ["dense/model_optimized.onnx", "dense/tokenizer.json", "sparse/english.txt"],
)
def test_missing_model_assets_fail_verification(assets: Path, relative: str) -> None:
    (assets / relative).unlink()
    with pytest.raises(ValueError, match="embedding_asset_inventory"):
        verify_embedding_assets(assets)


@pytest.mark.parametrize("content", [b"", b"larger than the registered file" * 5])
def test_asset_size_mismatch_rejects_before_reading_contents(
    assets: Path, content: bytes
) -> None:
    (assets / "sparse/english.txt").write_bytes(content)
    with pytest.raises(ValueError, match="embedding_asset_changed"):
        verify_embedding_assets(assets)


@pytest.mark.parametrize(
    "relative", ["dense/external.onnx", "sparse/custom.txt", "unregistered.json"]
)
def test_extra_files_are_not_silently_used(assets: Path, relative: str) -> None:
    (assets / relative).write_text("not registered")
    with pytest.raises(ValueError, match="embedding_asset_inventory"):
        verify_embedding_assets(assets)


def test_directory_cannot_replace_a_registered_file(assets: Path) -> None:
    path = assets / "sparse/english.txt"
    path.unlink()
    path.mkdir()
    with pytest.raises(ValueError, match="embedding_asset_path"):
        verify_embedding_assets(assets)


@pytest.mark.parametrize("path", [Path("models"), Path("missing")])
def test_relative_model_paths_reject(path: Path) -> None:
    with pytest.raises(ValueError, match="embedding_asset_path"):
        verify_embedding_assets(path)


def test_missing_absolute_model_root_rejects(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="embedding_asset_path"):
        verify_embedding_assets(tmp_path / "missing")


def test_parent_traversal_model_path_rejects(assets: Path) -> None:
    with pytest.raises(ValueError, match="embedding_asset_path"):
        verify_embedding_assets(assets / "dense" / "..")


@pytest.mark.parametrize("component", ["root", "dense", "sparse"])
def test_linked_model_directory_rejects(
    assets: Path, tmp_path: Path, component: str
) -> None:
    source = assets if component == "root" else assets / component
    target = tmp_path / "link"
    try:
        target.symlink_to(source, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(target), str(source)],
            check=True,
            capture_output=True,
        )
    if component == "root":
        root = target
    else:
        root = tmp_path / "linked-models"
        root.mkdir()
        if os.name != "nt":
            (root / component).symlink_to(target, target_is_directory=True)
        else:
            subprocess.run(
                [
                    "cmd.exe",
                    "/d",
                    "/c",
                    "mklink",
                    "/J",
                    str(root / component),
                    str(target),
                ],
                check=True,
                capture_output=True,
            )
        other = "sparse" if component == "dense" else "dense"
        shutil.copytree(assets / other, root / other)
    with pytest.raises(ValueError, match="embedding_asset_path"):
        verify_embedding_assets(root)


def test_different_fastembed_version_rejects(
    assets: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(models, "version", lambda name: "0.8.2", raising=False)
    with pytest.raises(ValueError, match="embedding_package_changed"):
        verify_embedding_assets(assets)


def test_bad_assets_reject_before_constructing_a_model(
    assets: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fastembed

    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("model constructed before asset verification")

    monkeypatch.setattr(fastembed, "TextEmbedding", unexpected)
    monkeypatch.setattr(fastembed, "SparseTextEmbedding", unexpected)
    (assets / "dense/tokenizer.json").write_bytes(b"changed")
    with pytest.raises(ValueError, match="embedding_asset_changed"):
        PinnedEmbeddingProvider(assets)


def test_pinned_provider_uses_offline_paths_and_production_embedding_methods(
    assets: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fastembed

    class Dense:
        def __init__(self, model: str, **options: Any) -> None:
            assert model == "BAAI/bge-small-en-v1.5"
            assert options == {
                "cache_dir": str(assets),
                "specific_model_path": str(assets / "dense"),
                "local_files_only": True,
                "threads": 2,
                "providers": ["CPUExecutionProvider"],
                "cuda": False,
            }

        def passage_embed(self, texts: list[str]) -> list[list[float]]:
            assert texts == ["first", "second"]
            return [[0.25] * 384, [0.5] * 384]

        def query_embed(self, text: str) -> list[list[float]]:
            assert text == "question"
            return [[-0.25] * 384]

    class Sparse:
        def __init__(self, model: str, **options: Any) -> None:
            assert model == "Qdrant/bm25"
            assert options == {
                "cache_dir": str(assets),
                "specific_model_path": str(assets / "sparse"),
                "local_files_only": True,
                "threads": 2,
                "k": 1.2,
                "b": 0.75,
                "avg_len": 256.0,
                "language": "english",
                "token_max_length": 40,
                "disable_stemmer": False,
            }

        def passage_embed(self, texts: list[str]) -> list[SimpleNamespace]:
            assert texts == ["first", "second"]
            return [
                SimpleNamespace(indices=[3], values=[1.5]),
                SimpleNamespace(indices=[9], values=[2.5]),
            ]

        def query_embed(self, text: str) -> list[SimpleNamespace]:
            assert text == "question"
            return [SimpleNamespace(indices=[3, 9], values=[1.0, 1.0])]

    monkeypatch.setattr(fastembed, "TextEmbedding", Dense)
    monkeypatch.setattr(fastembed, "SparseTextEmbedding", Sparse)
    provider = PinnedEmbeddingProvider(assets)
    documents = provider.documents(["first", "second"])
    assert documents[0].dense == (0.25,) * 384
    assert documents[0].indices == (3,)
    assert documents[0].values == (1.5,)
    assert documents[1].dense == (0.5,) * 384
    assert documents[1].indices == (9,)
    assert documents[1].values == (2.5,)
    query = provider.query("question")
    assert query.dense == (-0.25,) * 384
    assert query.indices == (3, 9)
    assert query.values == (1.0, 1.0)
    assert provider.fingerprint == verify_embedding_assets(assets).fingerprint


def test_committed_pins_are_complete_and_have_distinct_inventory() -> None:
    pins = load_embedding_pins()
    assert len(pins.dense.files) == 8
    assert len(pins.sparse.files) == 3
    assert pins.dense.model == "BAAI/bge-small-en-v1.5"
    assert pins.sparse.model == "Qdrant/bm25"
    assert len(pins.fingerprint) == 64
    assert EmbeddingPins.model_validate(pins.model_dump()) == pins


@pytest.mark.parametrize(
    "changes",
    [
        {"model": "another/model"},
        {"repository": "another/model"},
        {"license": "apache-2.0"},
        {"revision": "main"},
    ],
)
def test_pin_manifest_rejects_unbound_models(changes: dict[str, str]) -> None:
    pins = load_embedding_pins()
    invalid = pins.model_dump() | {"dense": pins.dense.model_dump() | changes}
    with pytest.raises(ValidationError):
        EmbeddingPins.model_validate(invalid)


@pytest.mark.parametrize("change", ["duplicate", "missing", "foreign"])
def test_pin_manifest_rejects_incomplete_inventory(change: str) -> None:
    pins = load_embedding_pins()
    files = [asset.model_dump() for asset in pins.dense.files]
    if change == "duplicate":
        files[-1] = files[0]
    elif change == "missing":
        files.pop()
    else:
        files[0] = files[0] | {"name": ".."}
    invalid = pins.model_dump() | {"dense": pins.dense.model_dump() | {"files": files}}
    with pytest.raises(ValidationError, match="embedding_pin_inventory"):
        EmbeddingPins.model_validate(invalid)


def test_pin_manifest_read_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(models, "__file__", str(tmp_path / "models.py"))
    (tmp_path / "embedding-pins.json").write_bytes(b" " * (16 * 1024 + 1))
    with pytest.raises(ValueError, match="embedding_pin_size"):
        load_embedding_pins()
