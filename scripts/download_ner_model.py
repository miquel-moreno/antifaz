"""Download the NER model pinned in src/antifaz/detect/ner/manifest.json (`make ner-model`).

Every file comes over https from `https://huggingface.co/<repo>/resolve/<commit>/<file>`, a
fixed commit (never a branch), and is checked by size and SHA-256 against the manifest before
it gets its final name. A download is written to "<file>.part" next to its place and renamed
only once it matches: a failed or interrupted download (an error, Ctrl+C) deletes it. Only a
hard kill of the process (power cut, `kill -9`) can leave a ".part" file; the gateway then
refuses the directory (a file that is not in the manifest) and a new run overwrites it. A file
already in place with the right hash is not downloaded again; one with other content is an
error (it is never overwritten silently).

The gateway never downloads anything: this command is the only way the model arrives
(ADR-0016). Destination: --dest, else ANTIFAZ_NER_MODEL_DIR, else models/gliner_multi_pii-v1.

    uv run python -m scripts.download_ner_model [--dest DIR]
"""

import argparse
import hashlib
import json
import re
import sys
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO, Any

from antifaz.config import Settings
from antifaz.detect.ner.manifest import DEFAULT_MANIFEST, ModelMismatchError, load_manifest

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEST = ROOT / "models" / "gliner_multi_pii-v1"
HUB = "https://huggingface.co"
_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_BLOCK = 1024 * 1024
_PROGRESS_EVERY = 100 * _BLOCK
TIMEOUT_SECONDS = 60.0

Opener = Callable[..., Any]  # urllib.request.urlopen(url, timeout=...) or a test fake


class DownloadError(Exception):
    """The manifest, the server or a file is not what was reviewed."""


@dataclass(frozen=True, slots=True)
class Download:
    name: str  # path inside the model directory
    url: str
    size: int
    sha256: str


def _safe_relative(name: object) -> bool:
    if not isinstance(name, str) or not name or "\\" in name:
        return False
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts and str(path) == name


def planned_downloads(manifest_path: Path = DEFAULT_MANIFEST) -> list[Download]:
    """Every file of the manifest with its pinned URL. Each file needs exactly one source."""
    try:
        manifest = load_manifest(manifest_path)
        sources = json.loads(manifest_path.read_bytes()).get("sources")
    except (ModelMismatchError, OSError, ValueError) as error:
        raise DownloadError(f"cannot read the manifest: {error}") from None
    if not isinstance(sources, list):
        raise DownloadError("the manifest has no list of sources")
    items: dict[str, Download] = {}
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get("files"), dict):
            raise DownloadError("a source of the manifest is malformed")
        repo, revision = source.get("repo"), source.get("revision")
        if not isinstance(repo, str) or not _REPO.fullmatch(repo) or ".." in repo:
            raise DownloadError("a source of the manifest has a bad repository name")
        if not isinstance(revision, str) or not _COMMIT.fullmatch(revision):
            raise DownloadError(f"{repo}: the revision must be a full commit hash")
        for name, remote in source["files"].items():
            if not _safe_relative(name) or not _safe_relative(remote):
                raise DownloadError(f"{repo}: a file path is not a plain relative path")
            entry = manifest.files.get(name)
            if entry is None or name in items:
                raise DownloadError(f"{name}: not in the manifest files, or listed twice")
            url = f"{HUB}/{repo}/resolve/{revision}/{remote}"
            items[name] = Download(name, url, entry.size, entry.sha256)
    missing = sorted(set(manifest.files) - set(items))
    if missing:
        raise DownloadError(f"{missing[0]}: no source to download it from")
    return sorted(items.values(), key=lambda item: (item.size, item.name))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while block := file.read(_BLOCK):
            digest.update(block)
    return digest.hexdigest()


def _matches(path: Path, item: Download) -> bool:
    return path.stat().st_size == item.size and _sha256(path) == item.sha256


def _copy(response: IO[bytes], file: IO[bytes], item: Download) -> str:
    """Copy at most item.size + 1 bytes and return their SHA-256."""
    digest = hashlib.sha256()
    written = 0
    next_report = _PROGRESS_EVERY
    while block := response.read(min(_BLOCK, item.size + 1 - written)):
        file.write(block)
        digest.update(block)
        written += len(block)
        if written >= next_report:
            print(f"  {item.name}: {written // _BLOCK} / {item.size // _BLOCK} MB", flush=True)
            next_report += _PROGRESS_EVERY
        if written > item.size:
            break
    if written != item.size:
        raise DownloadError(f"{item.name}: unexpected size; nothing was saved")
    return digest.hexdigest()


def download_file(item: Download, dest: Path, opener: Opener = urllib.request.urlopen) -> bool:
    """Download one file if it is not there yet. True if it was downloaded."""
    path = dest / item.name
    if path.exists():
        if path.is_symlink() or not path.is_file() or not _matches(path, item):
            raise DownloadError(
                f"{item.name}: a different file is already there; delete it and run again"
            )
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    try:
        # The URL is built from the reviewed manifest (https, fixed commit), never user input.
        with opener(item.url, timeout=TIMEOUT_SECONDS) as response:
            if not str(response.geturl()).startswith("https://"):
                raise DownloadError(f"{item.name}: the server redirected away from https")
            with partial.open("wb") as file:
                digest = _copy(response, file, item)
        if digest != item.sha256:
            raise DownloadError(f"{item.name}: unexpected SHA-256; nothing was saved")
        partial.replace(path)
    except BaseException:
        partial.unlink(missing_ok=True)  # never leave a half or bad download behind
        raise
    return True


def download_all(
    manifest_path: Path, dest: Path, opener: Opener = urllib.request.urlopen
) -> list[str]:
    """Every file of the manifest in `dest`; the names that were downloaded now."""
    downloaded = []
    for item in planned_downloads(manifest_path):
        print(f"{item.name} ({item.size:,} bytes)", flush=True)
        if download_file(item, dest, opener):
            downloaded.append(item.name)
    return downloaded


def destination(given: str | None) -> Path:
    if given:
        return Path(given)
    configured = Settings().ner_model_dir
    return configured if configured is not None else DEFAULT_DEST


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download the pinned NER model.")
    parser.add_argument("--dest", help="model directory (default: ANTIFAZ_NER_MODEL_DIR)")
    args = parser.parse_args(argv)
    dest = destination(args.dest)
    try:
        downloaded = download_all(DEFAULT_MANIFEST, dest)
    except DownloadError as error:
        print(f"Download failed: {error}", file=sys.stderr)
        return 1
    print(f"Model ready in {dest} ({len(downloaded)} file(s) downloaded now).")
    print(f"Set ANTIFAZ_NER_MODEL_DIR={dest} and ANTIFAZ_NER_ENABLED=true to use it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
