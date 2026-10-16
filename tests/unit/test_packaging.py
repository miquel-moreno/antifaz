"""What the built wheel ships for the panel (issue 53, ADR-0018, PR 11c-1).

The wheel is built for real (`uv build --wheel`, into a temp folder) and read as a zip: every
static file the panel serves, the OFL text next to each font, and the three templates. The
image (tests/e2e/test_panel_static_image.py, `make e2e`) installs this same package.
"""

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from antifaz.web.assets import LICENCE_TEXTS, SERVED
from antifaz.web.templating import TEMPLATES

REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "src" / "antifaz" / "web" / "static"


@pytest.fixture(scope="module")
def wheel(tmp_path_factory: pytest.TempPathFactory) -> zipfile.ZipFile:
    uv = shutil.which("uv")
    if uv is None:  # pragma: no cover - uv runs every target of the Makefile and CI
        pytest.fail("uv is not on PATH")
    out = tmp_path_factory.mktemp("wheel")
    built = subprocess.run(  # noqa: S603 - a fixed program and arguments, never a shell
        [uv, "build", "--wheel", "--out-dir", str(out), str(REPO)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert built.returncode == 0, built.stderr[-2000:]
    (path,) = out.glob("antifaz-*.whl")
    return zipfile.ZipFile(path)


def test_the_wheel_ships_every_static_file_with_its_exact_bytes(wheel: zipfile.ZipFile) -> None:
    for name in SERVED:
        assert wheel.read(f"antifaz/web/static/{name}") == (STATIC / name).read_bytes(), name


def test_the_wheel_ships_each_ofl_text_next_to_its_font(wheel: zipfile.ZipFile) -> None:
    names = set(wheel.namelist())
    for font, licence in (
        ("InterVariable.woff2", "Inter-OFL.txt"),
        ("GeistMono-Variable.woff2", "GeistMono-OFL.txt"),
    ):
        assert f"antifaz/web/static/{font}" in names
        assert wheel.read(f"antifaz/web/static/{licence}") == (STATIC / licence).read_bytes()
    assert set(LICENCE_TEXTS) == {"Inter-OFL.txt", "GeistMono-OFL.txt"}


def test_the_wheel_ships_the_templates(wheel: zipfile.ZipFile) -> None:
    for name in TEMPLATES:
        assert f"antifaz/web/templates/{name}" in wheel.namelist(), name


def test_the_static_folder_in_the_wheel_holds_nothing_else(wheel: zipfile.ZipFile) -> None:
    shipped = {
        name.removeprefix("antifaz/web/static/")
        for name in wheel.namelist()
        if name.startswith("antifaz/web/static/")
    }
    assert shipped == set(SERVED) | set(LICENCE_TEXTS)
