"""The panel's static files (issue 53, ADR-0018, PR 11c-1).

The design assets live in one place, the package (src/antifaz/web/static/): the prototype in
design/ and the real panel load the same tokens.css, fonts and fonts.css. The fonts and their
OFL texts are the upstream files, byte for byte, with the SHA-256 recorded in design/README.md.
The logo stays at docs/images/logo.svg; static/logo.svg is an identical copy for the favicon.
"""

import hashlib
import re
from pathlib import Path

import pytest

import antifaz.web

STATIC = Path(antifaz.web.__file__).parent / "static"
REPO = Path(__file__).resolve().parents[2]
DESIGN_README = REPO / "design" / "README.md"
FONTS = ("InterVariable.woff2", "GeistMono-Variable.woff2")
LICENCES = ("Inter-OFL.txt", "GeistMono-OFL.txt")


def _recorded_sha256() -> dict[str, str]:
    """File name -> SHA-256 from the font table of design/README.md."""
    rows = re.findall(
        r"^\| `([^`]+)` \|.*\| `([0-9a-f]{64})` \|$", DESIGN_README.read_text("utf-8"), re.M
    )
    return dict(rows)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_design_readme_records_every_font_and_licence() -> None:
    assert set(_recorded_sha256()) == {*FONTS, *LICENCES}


@pytest.mark.parametrize("name", [*FONTS, *LICENCES])
def test_fonts_and_their_licences_are_the_upstream_files(name: str) -> None:
    assert _sha256(STATIC / name) == _recorded_sha256()[name]


def test_each_font_has_its_ofl_text_next_to_it() -> None:
    for font, licence in zip(FONTS, LICENCES, strict=True):
        assert (STATIC / font).is_file() and (STATIC / licence).is_file()
        assert "SIL OPEN FONT LICENSE" in (STATIC / licence).read_text("utf-8").upper()


def test_the_design_assets_have_one_home() -> None:
    """Nothing of them is left in design/: the prototype loads them from the package."""
    for name in ("tokens.css", "fonts", "fonts/fonts.css", *(f"fonts/{n}" for n in FONTS)):
        assert not (REPO / "design" / name).exists(), name
    for name in ("tokens.css", "fonts.css"):
        assert (STATIC / name).is_file(), name


def test_the_favicon_is_the_published_logo_byte_for_byte() -> None:
    assert (STATIC / "logo.svg").read_bytes() == (REPO / "docs/images/logo.svg").read_bytes()


def test_the_prototype_pages_load_the_package_assets() -> None:
    for page in ("antifaz-panel.html", "tokens-preview.html"):
        text = (REPO / "design" / "prototype" / page).read_text("utf-8")
        assert 'href="../../src/antifaz/web/static/fonts.css"' in text, page
        assert 'href="../../src/antifaz/web/static/tokens.css"' in text, page
        assert "../fonts/" not in text and 'href="../tokens.css"' not in text, page
