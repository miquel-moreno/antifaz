"""scripts/release_compose.py: the docker-compose.yml of a release, pinned by digest (issue 42)."""

import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml
from scripts.release_compose import main, pinned

REPO = Path(__file__).resolve().parents[2]
COMPOSE = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
VERSION: str = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"][
    "version"
]
DIGEST = "sha256:" + "0123456789abcdef" * 4


def _load(text: str) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(text)
    return data


def test_only_the_image_changes_and_it_is_pinned_by_digest() -> None:
    released = _load(pinned(COMPOSE, VERSION, DIGEST))
    original = _load(COMPOSE)
    image = released["services"]["antifaz"].pop("image")
    original["services"]["antifaz"].pop("image")
    assert image == f"ghcr.io/miquel-moreno/antifaz:{VERSION}@{DIGEST}"
    assert released == original


def test_the_released_file_has_no_variable_for_the_image() -> None:
    text = pinned(COMPOSE, VERSION, DIGEST)
    assert "${ANTIFAZ_VERSION" not in text
    assert text.startswith(f"# Antifaz {VERSION}, attached to the GitHub release v{VERSION}.")


@pytest.mark.parametrize(
    "digest",
    [
        "",
        "sha256:" + "0" * 63,
        "sha256:" + "0" * 65,
        "sha256:" + "A" * 64,
        "sha512:" + "0" * 64,
        DIGEST + "\nimage: evil",
        "0" * 64,
    ],
)
def test_a_malformed_digest_is_refused(digest: str) -> None:
    with pytest.raises(ValueError, match="digest"):
        pinned(COMPOSE, VERSION, digest)


@pytest.mark.parametrize("version", ["", "v0.2.0", "0.2", "0.2.0-rc1", "0.2.0\n"])
def test_a_malformed_version_is_refused(version: str) -> None:
    with pytest.raises(ValueError, match="version"):
        pinned(COMPOSE, version, DIGEST)


def test_a_version_other_than_the_file_is_refused() -> None:
    with pytest.raises(ValueError, match="another version"):
        pinned(COMPOSE, "9.9.9", DIGEST)


@pytest.mark.parametrize(
    "compose",
    [
        "services:\n  antifaz:\n    image: antifaz:local\n",
        COMPOSE + "  other:\n" + COMPOSE.split("services:\n  antifaz:\n", 1)[1],
    ],
)
def test_zero_or_two_image_lines_are_refused(compose: str) -> None:
    with pytest.raises(ValueError, match="image line"):
        pinned(compose, VERSION, DIGEST)


def test_main_writes_the_file_with_lf(tmp_path: Path) -> None:
    output = tmp_path / "docker-compose.yml"
    assert main(["--version", VERSION, "--digest", DIGEST, "--output", str(output)]) == 0
    data = output.read_bytes()
    assert b"\r\n" not in data
    assert f"@{DIGEST}".encode() in data


def test_main_fails_without_writing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    output = tmp_path / "docker-compose.yml"
    assert main(["--version", VERSION, "--digest", "sha256:bad", "--output", str(output)]) == 1
    assert not output.exists()
    assert "digest" in capsys.readouterr().err
