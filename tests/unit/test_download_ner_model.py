"""`make ner-model`: the pinned model files, over https, checked by size and SHA-256.

No network here: the opener is a fake that serves synthetic bytes.
"""

import hashlib
import io
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from scripts import download_ner_model as dl

from antifaz.detect.ner.manifest import DEFAULT_MANIFEST, load_manifest, verify_model_dir

WEIGHTS = b"synthetic weights, not a model" * 100
CONFIG = b'{"max_len": 384}'
COMMIT = "a" * 40


def _entry(content: bytes) -> dict[str, object]:
    return {"size": len(content), "sha256": hashlib.sha256(content).hexdigest()}


def _manifest(tmp_path: Path, **changes: object) -> Path:
    data: dict[str, object] = {
        "format": 1,
        "model": "example/model",
        "revision": COMMIT,
        "license": "Apache-2.0",
        "sources": [
            {
                "repo": "example/model",
                "revision": COMMIT,
                "license": "Apache-2.0",
                "files": {"weights.bin": "weights.bin", "tok/config.json": "config.json"},
            }
        ],
        "files": {"weights.bin": _entry(WEIGHTS), "tok/config.json": _entry(CONFIG)},
    }
    data.update(changes)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class FakeResponse(io.BytesIO):
    def __init__(self, content: bytes, url: str) -> None:
        super().__init__(content)
        self._url = url

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def _opener(
    served: dict[str, bytes], calls: list[str], final_url: Callable[[str], str] = lambda u: u
) -> Callable[..., FakeResponse]:
    def open_url(url: str, timeout: float) -> FakeResponse:
        calls.append(url)
        name = url.rsplit("/", 1)[1]
        return FakeResponse(served[name], final_url(url))

    return open_url


def test_the_shipped_manifest_downloads_every_file_it_checks() -> None:
    items = dl.planned_downloads(DEFAULT_MANIFEST)
    manifest = load_manifest(DEFAULT_MANIFEST)

    assert {item.name for item in items} == set(manifest.files)
    for item in items:
        assert item.url.startswith("https://huggingface.co/")
        assert "/resolve/" in item.url
        assert (item.size, item.sha256) == (
            manifest.files[item.name].size,
            manifest.files[item.name].sha256,
        )
    weights = next(item for item in items if item.name == "pytorch_model.bin")
    assert weights.url == (
        "https://huggingface.co/urchade/gliner_multi_pii-v1/resolve/"
        "1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d/pytorch_model.bin"
    )


def test_downloads_verifies_and_leaves_exactly_the_manifest_files(tmp_path: Path) -> None:
    manifest_path = _manifest(tmp_path)
    dest = tmp_path / "model"
    calls: list[str] = []

    dl.download_all(
        manifest_path, dest, _opener({"weights.bin": WEIGHTS, "config.json": CONFIG}, calls)
    )

    assert (dest / "weights.bin").read_bytes() == WEIGHTS
    assert (dest / "tok" / "config.json").read_bytes() == CONFIG
    assert calls == [
        f"https://huggingface.co/example/model/resolve/{COMMIT}/config.json",
        f"https://huggingface.co/example/model/resolve/{COMMIT}/weights.bin",
    ]
    verify_model_dir(dest, load_manifest(manifest_path))  # no partial or extra files


def test_files_already_there_and_correct_are_not_downloaded_again(tmp_path: Path) -> None:
    manifest_path = _manifest(tmp_path)
    dest = tmp_path / "model"
    served = {"weights.bin": WEIGHTS, "config.json": CONFIG}
    dl.download_all(manifest_path, dest, _opener(served, []))
    calls: list[str] = []

    dl.download_all(manifest_path, dest, _opener(served, calls))

    assert calls == []


@pytest.mark.parametrize(
    "bad",
    [WEIGHTS[:-1], WEIGHTS + b"x", b"y" + WEIGHTS[1:]],
    ids=["short", "long", "other bytes"],
)
def test_a_wrong_download_is_refused_and_leaves_nothing(tmp_path: Path, bad: bytes) -> None:
    manifest_path = _manifest(tmp_path)
    dest = tmp_path / "model"

    with pytest.raises(dl.DownloadError):
        dl.download_all(
            manifest_path, dest, _opener({"weights.bin": bad, "config.json": CONFIG}, [])
        )

    assert not (dest / "weights.bin").exists()
    assert not [p for p in dest.rglob("*") if p.name.endswith(".part")]


def test_an_existing_file_with_other_content_is_not_overwritten(tmp_path: Path) -> None:
    manifest_path = _manifest(tmp_path)
    dest = tmp_path / "model"
    dest.mkdir()
    (dest / "weights.bin").write_bytes(b"something else")

    with pytest.raises(dl.DownloadError, match=r"weights\.bin"):
        dl.download_all(manifest_path, dest, _opener({"config.json": CONFIG}, []))

    assert (dest / "weights.bin").read_bytes() == b"something else"


def test_a_redirect_away_from_https_is_refused(tmp_path: Path) -> None:
    manifest_path = _manifest(tmp_path)
    served = {"weights.bin": WEIGHTS, "config.json": CONFIG}

    with pytest.raises(dl.DownloadError, match="https"):
        dl.download_all(
            manifest_path,
            tmp_path / "model",
            _opener(served, [], final_url=lambda u: u.replace("https://", "http://")),
        )


def test_an_interrupted_download_leaves_no_partial_file(tmp_path: Path) -> None:
    manifest_path = _manifest(tmp_path)
    dest = tmp_path / "model"

    class Broken(FakeResponse):
        def read(self, size: int | None = -1) -> bytes:
            raise OSError("connection reset")

    def open_url(url: str, timeout: float) -> FakeResponse:
        return Broken(b"", url)

    with pytest.raises(OSError, match="connection reset"):
        dl.download_all(manifest_path, dest, open_url)

    assert not [p for p in dest.rglob("*") if p.is_file()]


@pytest.mark.parametrize(
    "sources",
    [
        [{"repo": "example/model", "revision": "main", "license": "x", "files": {}}],
        [{"repo": "../evil", "revision": COMMIT, "license": "x", "files": {}}],
        [
            {
                "repo": "example/model",
                "revision": COMMIT,
                "license": "x",
                "files": {"../weights.bin": "weights.bin"},
            }
        ],
        [
            {
                "repo": "example/model",
                "revision": COMMIT,
                "license": "x",
                "files": {"weights.bin": "../../other/weights.bin"},
            }
        ],
        [
            {
                "repo": "example/model",
                "revision": COMMIT,
                "license": "x",
                "files": {"weights.bin": "weights.bin"},  # tok/config.json has no source
            }
        ],
        "not a list",
    ],
    ids=["branch", "repo", "local path", "remote path", "missing file", "shape"],
)
def test_a_manifest_with_bad_sources_is_refused(tmp_path: Path, sources: object) -> None:
    with pytest.raises(dl.DownloadError):
        dl.planned_downloads(_manifest(tmp_path, sources=sources))


def test_the_destination_comes_from_the_settings_or_the_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("ANTIFAZ_NER_MODEL_DIR", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here
    assert dl.destination(None) == dl.DEFAULT_DEST
    monkeypatch.setenv("ANTIFAZ_NER_MODEL_DIR", str(tmp_path / "elsewhere"))
    assert dl.destination(None) == tmp_path / "elsewhere"
    assert dl.destination(str(tmp_path / "given")) == tmp_path / "given"
