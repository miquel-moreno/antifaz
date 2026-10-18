"""The panel's static files (issue 53, ADR-0018, PR 11c-1).

The design assets live in one place, the package (src/antifaz/web/static/): the prototype in
design/ and the real panel load the same tokens.css, fonts and fonts.css. The fonts and their
OFL texts are the upstream files, byte for byte, with the SHA-256 recorded in design/README.md.
The logo stays at docs/images/logo.svg; static/logo.svg is an identical copy for the favicon.

The manifest (web/assets.py): an explicit list of files, read once at startup, each published as
name.<12 hex of its SHA-256>.ext and kept in memory. Only .css, .js, .woff2 and .svg, each with
its fixed Content-Type. A CSS may only point (url()) to other files of the manifest, by their
hashed names; @import, data:, http(s):, "//", paths, symlinks and files outside the folder are
refused when the gateway starts, and so is an SVG that could run or fetch anything.
"""

import hashlib
import re
from pathlib import Path
from urllib.parse import unquote

import pytest

import antifaz.web
from antifaz.web.assets import (
    CONTENT_TYPES,
    LICENCE_TEXTS,
    SERVED,
    STATIC_PREFIX,
    AssetError,
    Assets,
    hashed_name,
    load_assets,
)

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


# --- The manifest: what is served, under which name, with which type ------------------------

ASSETS = load_assets()
SVG = "<svg xmlns='http://www.w3.org/2000/svg'/>"
CSS_URL = re.compile(r"url\(\s*([\"']?)([^\"')\s]*)\1\s*\)", re.IGNORECASE)


def _folder(tmp_path: Path, files: dict[str, str | bytes]) -> Path:
    for name, content in files.items():
        data = content.encode("utf-8") if isinstance(content, str) else content
        (tmp_path / name).write_bytes(data)
    return tmp_path


def _body(assets: Assets, logical: str) -> bytes:
    return assets.files[assets.urls[logical].removeprefix(STATIC_PREFIX)].body


def test_every_file_in_the_folder_is_served_or_a_licence_text() -> None:
    names = {path.name for path in STATIC.iterdir()}

    assert names == set(SERVED) | set(LICENCE_TEXTS)
    assert not set(SERVED) & set(LICENCE_TEXTS)
    assert set(LICENCE_TEXTS) == set(LICENCES)


def test_the_manifest_serves_each_logical_file_once_under_the_static_prefix() -> None:
    assert STATIC_PREFIX == "/panel/static/"
    assert set(ASSETS.urls) == set(SERVED)
    assert len(ASSETS.files) == len(SERVED)
    for logical, url in ASSETS.urls.items():
        name = url.removeprefix(STATIC_PREFIX)
        assert url == STATIC_PREFIX + name and "/" not in name, logical
        assert name in ASSETS.files, logical
        assert ASSETS.get(name) is ASSETS.files[name]
    assert ASSETS.paths == frozenset(ASSETS.urls.values())
    assert ASSETS.get("panel.css") is None  # only the hashed name, never the plain one


def test_a_hashed_name_carries_12_hex_of_the_sha256_of_its_bytes() -> None:
    digest = hashlib.sha256(b"body{}").hexdigest()[:12]

    assert hashed_name("panel.css", b"body{}") == f"panel.{digest}.css"
    font = hashed_name("GeistMono-Variable.woff2", b"x")
    assert font == f"GeistMono-Variable.{hashlib.sha256(b'x').hexdigest()[:12]}.woff2"


def test_the_hash_is_stable_and_follows_the_content(tmp_path: Path) -> None:
    folder = _folder(tmp_path, {"a.css": "body{color:red}"})
    first = load_assets(folder, ["a.css"]).urls["a.css"]

    assert load_assets(folder, ["a.css"]).urls["a.css"] == first
    (folder / "a.css").write_text("body{color:blue}", encoding="utf-8")
    assert load_assets(folder, ["a.css"]).urls["a.css"] != first


def test_a_css_points_to_the_hashed_name_and_changes_when_that_file_changes(
    tmp_path: Path,
) -> None:
    folder = _folder(tmp_path, {"g.svg": SVG, "a.css": 'b{background:url("g.svg")}'})
    assets = load_assets(folder, ["g.svg", "a.css"])
    svg_name = assets.urls["g.svg"].removeprefix(STATIC_PREFIX)

    assert _body(assets, "a.css").decode() == f'b{{background:url("{svg_name}")}}'
    (folder / "g.svg").write_text(SVG.replace("/>", "></svg>"), encoding="utf-8")
    assert load_assets(folder, ["g.svg", "a.css"]).urls["a.css"] != assets.urls["a.css"]


def test_only_four_extensions_each_with_its_fixed_content_type() -> None:
    assert CONTENT_TYPES == {
        ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".woff2": "font/woff2",
        ".svg": "image/svg+xml",
    }
    for name, asset in ASSETS.files.items():
        assert asset.content_type == CONTENT_TYPES[Path(name).suffix], name


@pytest.mark.parametrize(
    "name",
    [
        "x.html",
        "x.txt",
        "x.py",
        "x.map",
        "x.js.map",
        "x",
        ".css",
        "x.CSS",
        "a.b.css",
        "../x.css",
        "sub/x.css",
        "sub\\x.css",
        "x .css",
        "Inter-OFL.txt",
        "templates",
    ],
)
def test_the_loader_refuses_any_other_name(tmp_path: Path, name: str) -> None:
    with pytest.raises(AssetError):
        load_assets(tmp_path, [name])


def test_the_loader_refuses_a_missing_file_and_a_repeated_name(tmp_path: Path) -> None:
    with pytest.raises(AssetError):
        load_assets(tmp_path, ["absent.css"])
    with pytest.raises(AssetError):
        load_assets(_folder(tmp_path, {"a.css": "a{}"}), ["a.css", "a.css"])


def test_every_served_css_url_points_into_the_manifest() -> None:
    found = 0
    for name, asset in ASSETS.files.items():
        if not name.endswith(".css"):
            continue
        text = asset.body.decode("utf-8")
        assert "@import" not in text.lower() and "data:" not in text.lower(), name
        assert text.lower().count("url(") == len(CSS_URL.findall(text)), name
        for _, target in CSS_URL.findall(text):
            found += 1
            assert target in ASSETS.files and not target.endswith(".css"), (name, target)
            assert ":" not in target and "/" not in target, (name, target)
    assert found >= 3  # the two fonts and the grain


@pytest.mark.parametrize(
    "css",
    [
        '@import "b.css";',
        "@IMPORT url(g.svg);",
        'a{background:url("data:image/svg+xml,<svg/>")}',
        "a{background:url(http://example.invalid/g.svg)}",
        "a{background:url('https://example.invalid/g.svg')}",
        "a{background:url(//example.invalid/g.svg)}",
        'a{background:url("../g.svg")}',
        'a{background:url("/panel/static/g.svg")}',
        'a{background:url("sub/g.svg")}',
        'a{background:url("missing.svg")}',
        'a{background:url("b.css")}',
        'a{background:url("#n")}',
        'a{background:url("javascript:x")}',
        'a{background:url( "g.svg)}',
        "a{background:URL(g.svg) url(}",
    ],
)
def test_the_loader_refuses_a_css_that_reaches_outside_the_manifest(
    tmp_path: Path, css: str
) -> None:
    folder = _folder(tmp_path, {"g.svg": SVG, "b.css": "b{}", "a.css": css})

    with pytest.raises(AssetError):
        load_assets(folder, ["g.svg", "b.css", "a.css"])


@pytest.mark.parametrize(
    "svg",
    [
        "<svg><script>alert(1)</script></svg>",
        "<svg><SCRIPT href='x.js'/></svg>",
        '<svg onload="alert(1)"/>',
        "<svg><rect ONCLICK='x'/></svg>",
        "<svg><foreignObject><p>x</p></foreignObject></svg>",
        '<svg><a href="javascript:alert(1)"><rect/></a></svg>',
        '<svg><image href="https://example.invalid/x.png"/></svg>',
        '<svg><use xlink:href="other.svg#x"/></svg>',
    ],
)
def test_the_loader_refuses_an_svg_that_could_run_or_fetch_anything(
    tmp_path: Path, svg: str
) -> None:
    with pytest.raises(AssetError):
        load_assets(_folder(tmp_path, {"x.svg": svg}), ["x.svg"])


def test_a_script_is_served_as_it_is_with_its_type(tmp_path: Path) -> None:
    """The scripts of 11c-2: no rewriting, only the hash and the fixed type."""
    assets = load_assets(_folder(tmp_path, {"panel.js": "export {};\n"}), ["panel.js"])

    assert _body(assets, "panel.js") == b"export {};\n"
    asset = assets.files[assets.urls["panel.js"].removeprefix(STATIC_PREFIX)]
    assert asset.content_type == "text/javascript; charset=utf-8"


def test_text_assets_must_be_utf8(tmp_path: Path) -> None:
    with pytest.raises(AssetError):
        load_assets(_folder(tmp_path, {"a.css": b"a{content:'\xff'}"}), ["a.css"])


def test_the_loader_refuses_a_symlink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outside = tmp_path / "outside.css"
    outside.write_text("a{}", encoding="utf-8")
    folder = tmp_path / "static"
    folder.mkdir()
    link = folder / "a.css"
    try:
        link.symlink_to(outside)
    except OSError:  # Windows without the symlink privilege: a file that says it is one
        link.write_text("a{}", encoding="utf-8")
        real = Path.is_symlink
        monkeypatch.setattr(Path, "is_symlink", lambda self: self == link or real(self))

    with pytest.raises(AssetError):
        load_assets(folder, ["a.css"])


def test_the_loader_refuses_a_file_that_resolves_outside_the_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _folder(tmp_path, {"a.css": "a{}"})
    real = Path.resolve
    elsewhere = tmp_path.parent / "elsewhere.css"

    def resolve(self: Path, strict: bool = False) -> Path:
        return elsewhere if self.name == "a.css" else real(self, strict)

    monkeypatch.setattr(Path, "resolve", resolve)

    with pytest.raises(AssetError):
        load_assets(folder, ["a.css"])


def test_the_manifest_is_read_only() -> None:
    first = next(iter(ASSETS.files))
    with pytest.raises(TypeError):
        ASSETS.files["x.css"] = ASSETS.files[first]  # type: ignore[index]
    with pytest.raises(TypeError):
        ASSETS.urls["x.css"] = "/panel/static/x.css"  # type: ignore[index]


def test_text_assets_are_utf8_without_bom_and_with_lf() -> None:
    for name in SERVED:
        if name.endswith((".css", ".svg", ".js")):
            data = (STATIC / name).read_bytes()
            assert not data.startswith(b"\xef\xbb\xbf") and b"\r" not in data, name
            data.decode("utf-8")


def test_the_grain_is_the_prototype_grain_as_a_file() -> None:
    page = (REPO / "design" / "prototype" / "antifaz-panel.html").read_text("utf-8")
    match = re.search(r'url\("data:image/svg\+xml,([^"]+)"\)', page)
    assert match is not None

    assert (STATIC / "grain.svg").read_text("utf-8").strip() == unquote(match.group(1))


def test_the_panel_css_leaves_out_what_only_the_prototype_needs() -> None:
    css = (STATIC / "panel.css").read_text("utf-8")
    for part in (
        ".proto-",
        ".chips",
        ".stage-wrap",
        ".annex",
        ".lset",
        ".lrow",
        ".intro",
        ".reduce-motion",
        ".glider",
        ".gtrack",
        ".tok-a",
        ".tok-ghost",
        "data:",
        "@import",
    ):
        assert part not in css, part
    for kept in (".app-window", ".journey", ".marker", ".keys", ".prov", ".ident", "grain.svg"):
        assert kept in css, kept


# --- Review of 11c-1: allow-lists instead of deny-lists, and no comments served --------------

SVG_ATTACKS = [
    # SMIL that turns a link into javascript: once the page runs it
    r"<svg xmlns='http://www.w3.org/2000/svg'><a><set attributeName='href' "
    r"to='&#106;avascript:alert(1)'/><rect width='1' height='1'/></a></svg>",
    r"<svg xmlns='http://www.w3.org/2000/svg'><style>@import url(https://example.invalid/x.css);"
    r"</style></svg>",
    r"<svg xmlns='http://www.w3.org/2000/svg'><rect style='fill:url(https://example.invalid/a)'"
    r"/></svg>",
    r"<svg xmlns='http://www.w3.org/2000/svg'><rect fill='url(https://example.invalid/x.svg#a)'"
    r"/></svg>",
    r"<!DOCTYPE svg [<!ENTITY x 'y'>]><svg xmlns='http://www.w3.org/2000/svg'>&x;</svg>",
    r"<svg xmlns='http://www.w3.org/2000/svg'><rect><set attributeName='onmouseover' "
    r"to='alert(1)'/></rect></svg>",
    r"<svg xmlns='http://www.w3.org/2000/svg'><iframe xmlns='http://www.w3.org/1999/xhtml' "
    r"src='https://example.invalid/'/></svg>",
    r"<svg/onload=alert(1)>",
    r"<svg xmlns='http://www.w3.org/2000/svg'><animate attributeName='x' to='1'/></svg>",
    r"<svg xmlns='http://www.w3.org/2000/svg'><rect width='1' unknown='x'/></svg>",
    r"<svg xmlns='http://www.w3.org/2000/svg'><use href='other.svg#a'/></svg>",
    "not xml at all <",
    r"<?xml-stylesheet href='https://example.invalid/x.css'?>"
    r"<svg xmlns='http://www.w3.org/2000/svg'/>",
]


@pytest.mark.parametrize("svg", SVG_ATTACKS)
def test_the_svg_allow_list_refuses_every_attack(tmp_path: Path, svg: str) -> None:
    with pytest.raises(AssetError):
        load_assets(_folder(tmp_path, {"x.svg": svg}), ["x.svg"])


@pytest.mark.parametrize(
    "svg",
    [
        "<svg xmlns='http://www.w3.org/2000/svg'><defs><symbol id='a'><path d='M0 0'/>"
        "</symbol></defs><use href='#a'/></svg>",
        "<svg xmlns='http://www.w3.org/2000/svg' xmlns:xlink='http://www.w3.org/1999/xlink'>"
        "<defs><path id='a' d='M0 0'/></defs><use xlink:href='#a'/></svg>",
        "<svg xmlns='http://www.w3.org/2000/svg'><defs><linearGradient id='g'>"
        "<stop offset='0' stop-color='#fff'/></linearGradient></defs>"
        "<rect width='1' height='1' fill='url(#g)'/></svg>",
        "<?xml version='1.0' encoding='UTF-8'?><svg xmlns='http://www.w3.org/2000/svg'/>",
    ],
)
def test_the_svg_allow_list_accepts_fragment_links(tmp_path: Path, svg: str) -> None:
    assets = load_assets(_folder(tmp_path, {"x.svg": svg}), ["x.svg"])

    assert len(assets.files) == 1


@pytest.mark.parametrize(
    "css",
    [
        r"a{background:u\72l(https://example.invalid/x.png)}",
        r"@\69mport 'https://example.invalid/x.css';",
        "a{background:image-set('https://example.invalid/x.png' 1x)}",
        "a{background:-webkit-image-set('g.svg' 1x)}",
        "@namespace svg url(g.svg);",
        "@font-feature-values Inter{}",
        "@page{margin:0}",
        "@supports (display:grid){a{}}",
    ],
)
def test_the_css_allow_list_refuses_every_attack(tmp_path: Path, css: str) -> None:
    folder = _folder(tmp_path, {"g.svg": "<svg xmlns='http://www.w3.org/2000/svg'/>", "a.css": css})

    with pytest.raises(AssetError):
        load_assets(folder, ["g.svg", "a.css"])


def test_the_css_allow_list_accepts_the_at_rules_the_panel_uses(tmp_path: Path) -> None:
    css = (
        "@font-face{font-family:x;src:url(g.svg)}@media (min-width:1px){a{}}"
        "@container app (width<1px){a{}}@keyframes k{from{opacity:0}to{opacity:1}}"
    )
    folder = _folder(tmp_path, {"g.svg": "<svg xmlns='http://www.w3.org/2000/svg'/>", "a.css": css})

    assert len(load_assets(folder, ["g.svg", "a.css"]).files) == 2


def test_the_shipped_files_pass_the_allow_lists() -> None:
    assert set(load_assets().urls) == set(SERVED)  # logo.svg, grain.svg and the three CSS


def test_no_served_file_carries_a_comment_or_a_name() -> None:
    for name, asset in ASSETS.files.items():
        if name.endswith((".css", ".svg", ".js")):
            text = asset.body.decode("utf-8")
            assert "/*" not in text and "<!--" not in text, name
            assert "miquel" not in text.lower(), name


def test_comments_are_stripped_before_hashing(tmp_path: Path) -> None:
    plain = load_assets(_folder(tmp_path, {"a.css": "a{color:red}"}), ["a.css"])
    commented = load_assets(
        _folder(tmp_path, {"a.css": "/* note */a{color:red}/* another\n line */"}), ["a.css"]
    )

    assert commented.urls == plain.urls
    assert _body(commented, "a.css") == b"a{color:red}"


# --- Review of the 11c-2b captures: the light rectangle under the window --------------------
#
# The window's shadow had layers with 100-120 px of blur. On the near-flat light background,
# Chromium draws such a wide, faint blur in visible rectangular bands: a lighter box stood out
# below the login card and the panel (checked by switching the shadow, the backdrop blur, the
# grain and the ambient light off one by one; only the shadow made it). A visual test would be
# flaky; this keeps every blur of the shadow tokens at or under MAX_SHADOW_BLUR.
MAX_SHADOW_BLUR = 64
SHADOW_TOKEN = re.compile(r"(--shadow-[a-z-]+)\s*:\s*([^;]+);")
LENGTHS = re.compile(r"(-?\d+(?:\.\d+)?)px|(?<![\w.])0(?![\w.])")


def _blurs(value: str) -> list[float]:
    """The blur radius (third length) of each layer of a box-shadow value."""
    blurs = []
    for layer in re.split(r",(?![^(]*\))", value):
        lengths = [
            float(m.group(1) or 0) for m in LENGTHS.finditer(re.sub(r"rgb\([^)]*\)", "", layer))
        ]
        if len(lengths) >= 3:
            blurs.append(lengths[2])
    return blurs


def test_no_shadow_token_has_a_blur_that_bands() -> None:
    tokens = (STATIC / "tokens.css").read_text("utf-8")
    found = SHADOW_TOKEN.findall(tokens)
    assert any(name == "--shadow-window" for name, _ in found)
    for name, value in found:
        assert max(_blurs(value), default=0) <= MAX_SHADOW_BLUR, name


def test_the_blur_reader_reads_each_layer() -> None:
    assert _blurs("0 0 0 1px rgb(0 0 0 / 0.05), 0 60px 120px -40px rgb(30 24 80 / 0.22)") == [
        0,
        120,
    ]
