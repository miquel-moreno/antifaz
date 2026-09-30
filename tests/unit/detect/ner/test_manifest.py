"""The model manifest (ADR-0016): every file, its size and SHA-256, checked before loading."""

import hashlib
import json
from pathlib import Path

import pytest

from antifaz.detect.ner.manifest import (
    DEFAULT_MANIFEST,
    Manifest,
    ModelMismatchError,
    load_manifest,
    verify_model_dir,
)

WEIGHTS = b"not a real model: synthetic bytes for the test"
CONFIG = b'{"max_len": 384}'


def _entry(content: bytes) -> dict[str, object]:
    return {"size": len(content), "sha256": hashlib.sha256(content).hexdigest()}


def _manifest(tmp_path: Path, files: dict[str, bytes]) -> Path:
    path = tmp_path / "manifest.json"
    data = {
        "format": 1,
        "model": "example/model",
        "revision": "0" * 40,
        "license": "Apache-2.0",
        "files": {name: _entry(content) for name, content in files.items()},
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture
def model_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "model"
    (directory / "sub").mkdir(parents=True)
    (directory / "model.safetensors").write_bytes(WEIGHTS)
    (directory / "sub" / "config.json").write_bytes(CONFIG)
    return directory


@pytest.fixture
def manifest(tmp_path: Path) -> Manifest:
    return load_manifest(
        _manifest(tmp_path, {"model.safetensors": WEIGHTS, "sub/config.json": CONFIG})
    )


def test_the_shipped_manifest_is_a_placeholder_until_the_model_arrives() -> None:
    shipped = load_manifest(DEFAULT_MANIFEST)
    assert shipped.model == "urchade/gliner_multi_pii-v1"
    assert shipped.files == {}


def test_a_placeholder_manifest_refuses_any_directory(model_dir: Path) -> None:
    with pytest.raises(ModelMismatchError, match="no model files"):
        verify_model_dir(model_dir, load_manifest(DEFAULT_MANIFEST))


def test_a_matching_directory_passes(model_dir: Path, manifest: Manifest) -> None:
    verify_model_dir(model_dir, manifest)


def test_the_digest_changes_with_the_manifest(tmp_path: Path, manifest: Manifest) -> None:
    other = load_manifest(_manifest(tmp_path, {"model.safetensors": WEIGHTS + b"x"}))
    assert len(manifest.digest) == 64
    assert other.digest != manifest.digest


def test_a_missing_directory_is_refused(tmp_path: Path, manifest: Manifest) -> None:
    with pytest.raises(ModelMismatchError, match="not a directory"):
        verify_model_dir(tmp_path / "nowhere", manifest)


def test_a_missing_file_is_refused(model_dir: Path, manifest: Manifest) -> None:
    (model_dir / "sub" / "config.json").unlink()
    with pytest.raises(ModelMismatchError, match="missing"):
        verify_model_dir(model_dir, manifest)


def test_an_extra_file_is_refused(model_dir: Path, manifest: Manifest) -> None:
    (model_dir / "extra.py").write_text("print('x')", encoding="utf-8")
    with pytest.raises(ModelMismatchError, match="not in the manifest"):
        verify_model_dir(model_dir, manifest)


def test_a_file_of_another_size_is_refused(model_dir: Path, manifest: Manifest) -> None:
    (model_dir / "model.safetensors").write_bytes(WEIGHTS + b"!")
    with pytest.raises(ModelMismatchError, match="size"):
        verify_model_dir(model_dir, manifest)


def test_a_file_with_the_same_size_but_other_bytes_is_refused(
    model_dir: Path, manifest: Manifest
) -> None:
    (model_dir / "model.safetensors").write_bytes(WEIGHTS[:-1] + b"?")
    with pytest.raises(ModelMismatchError, match="SHA-256"):
        verify_model_dir(model_dir, manifest)


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"format": 2, "model": "m", "revision": None, "license": "x", "files": {}},
        {"format": 1, "model": "", "revision": None, "license": "x", "files": {}},
        {"format": 1, "model": "m", "revision": None, "license": "x", "files": []},
        {"format": 1, "model": "m", "revision": 3, "license": "x", "files": {}},
        {"format": 1, "model": "m", "revision": None, "license": "x", "files": {"a": {}}},
        {
            "format": 1,
            "model": "m",
            "revision": None,
            "license": "x",
            "files": {"a": {"size": -1, "sha256": "0" * 64}},
        },
        {
            "format": 1,
            "model": "m",
            "revision": None,
            "license": "x",
            "files": {"a": {"size": True, "sha256": "0" * 64}},
        },
        {
            "format": 1,
            "model": "m",
            "revision": None,
            "license": "x",
            "files": {"a": {"size": 1, "sha256": "ABC"}},
        },
        {
            "format": 1,
            "model": "m",
            "revision": None,
            "license": "x",
            "files": {"../a": {"size": 1, "sha256": "0" * 64}},
        },
        {
            "format": 1,
            "model": "m",
            "revision": None,
            "license": "x",
            "files": {"/a": {"size": 1, "sha256": "0" * 64}},
        },
        {
            "format": 1,
            "model": "m",
            "revision": None,
            "license": "x",
            "files": {"a\\b": {"size": 1, "sha256": "0" * 64}},
        },
    ],
)
def test_a_malformed_manifest_is_refused(tmp_path: Path, data: object) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ModelMismatchError, match="manifest"):
        load_manifest(path)


def test_a_manifest_that_is_not_json_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ModelMismatchError, match="manifest"):
        load_manifest(path)
