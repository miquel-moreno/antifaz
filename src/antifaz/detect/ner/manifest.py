"""The model manifest: every file of the model with its size and SHA-256 (ADR-0016).

A model downloaded from the internet is third-party code and data: before loading, the model
directory must hold EXACTLY the files of the manifest (none missing, none extra, no symbolic
links) with the same sizes and hashes. Otherwise the gateway refuses to start. The manifest's
own digest goes into the NER cache key, so a new model never reuses old spans.

The shipped `manifest.json` is a placeholder without files until the model arrives (issue 6b):
it refuses every directory.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

DEFAULT_MANIFEST = Path(__file__).with_name("manifest.json")
FORMAT = 1
_SHA256 = re.compile(r"[0-9a-f]{64}")
_READ_BLOCK = 1024 * 1024


class ModelMismatchError(Exception):
    """The manifest or the model directory is not what was reviewed. Fixed reasons only."""


@dataclass(frozen=True, slots=True)
class FileEntry:
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class Manifest:
    model: str
    revision: str | None
    files: dict[str, FileEntry]
    digest: str  # SHA-256 of the manifest itself, for the cache key


def _safe_relative(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        bool(name)
        and "\\" not in name
        and not path.is_absolute()
        and ".." not in path.parts
        and str(path) == name
    )


def _entry(value: object) -> FileEntry | None:
    if not isinstance(value, dict) or set(value) != {"size", "sha256"}:
        return None
    size, sha256 = value["size"], value["sha256"]
    if type(size) is not int or size < 0:
        return None
    if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
        return None
    return FileEntry(size, sha256)


def _parse(data: object, digest: str) -> Manifest | None:
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        return None
    model, revision, files = data.get("model"), data.get("revision"), data.get("files")
    if not isinstance(model, str) or not model:
        return None
    if revision is not None and not isinstance(revision, str):
        return None
    if not isinstance(files, dict):
        return None
    entries: dict[str, FileEntry] = {}
    for name, value in files.items():
        entry = _entry(value)
        if entry is None or not _safe_relative(name):
            return None
        entries[name] = entry
    return Manifest(model, revision, entries, digest)


def load_manifest(path: Path = DEFAULT_MANIFEST) -> Manifest:
    try:
        raw = path.read_bytes()
        data = json.loads(raw)
    except (OSError, ValueError):
        data = None
        raw = b""
    manifest = _parse(data, hashlib.sha256(raw).hexdigest())
    if manifest is None:  # raised outside the except block: no parse error is chained
        raise ModelMismatchError("the model manifest is malformed")
    return manifest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while block := file.read(_READ_BLOCK):
            digest.update(block)
    return digest.hexdigest()


def _files(directory: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for path in directory.rglob("*"):
        name = path.relative_to(directory).as_posix()
        if path.is_symlink():
            raise ModelMismatchError(f"the model directory has a symbolic link: {name}")
        if path.is_file():
            found[name] = path
    return found


def verify_model_dir(directory: Path, manifest: Manifest) -> None:
    """Raise ModelMismatchError unless `directory` holds exactly the files of `manifest`."""
    if not manifest.files:
        raise ModelMismatchError("the manifest lists no model files: there is no model yet")
    if not directory.is_dir():
        raise ModelMismatchError("the model directory is not a directory")
    found = _files(directory)
    extra = sorted(set(found) - set(manifest.files))
    if extra:
        raise ModelMismatchError(f"file not in the manifest: {extra[0]}")
    for name, entry in sorted(manifest.files.items()):
        path = found.get(name)
        if path is None:
            raise ModelMismatchError(f"model file missing: {name}")
        if path.stat().st_size != entry.size:
            raise ModelMismatchError(f"model file with another size: {name}")
        if _sha256(path) != entry.sha256:
            raise ModelMismatchError(f"model file with another SHA-256: {name}")
