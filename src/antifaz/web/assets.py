"""The panel's static files (ADR-0018, PR 11c-1): a fixed list, read once, served from memory.

At startup load_assets() reads each file of SERVED from static/, removes its comments (CSS
/* ... */ and SVG <!-- ... -->: notes for whoever edits the source never reach a browser) and
publishes it as name.<first 12 hex of its SHA-256>.ext under /panel/static/. A new version gets a
new name, so a browser may keep a file for a year (Cache-Control: immutable, decided by the door)
and never sees a stale one. Inside a CSS, every url("x") is rewritten to the hashed name of x,
which must be another file of the list (and not a CSS); the CSS is hashed after that, so it
changes when a file it points to changes.

Allow-lists, refused at startup (the gateway does not start):
- Names: one plain file name with one of the four extensions (.css, .js, .woff2, .svg); never a
  symlink, never a file that resolves outside static/.
- CSS: no backslash at all (no escapes that hide a word), no image-set(), only the at-rules
  @font-face, @media, @container and @keyframes, no data:, and every url() exactly another
  listed file (no scheme, no "//", no path, no "#").
- SVG: no DOCTYPE or ENTITY and no processing instruction but the XML declaration; it must parse
  as XML (xml.etree, standard library); only the SVG elements and attributes of SVG_ELEMENTS and
  SVG_ATTRIBUTES (what logo.svg and grain.svg use, plus symbol/use for fragment links); href and
  xlink:href only as "#id" and url(...) only as url(#id). So no <script>, <style>, <set>,
  <animate>, <foreignObject>, no on... attribute and no style attribute.
- Text files that are not UTF-8.

Serving never touches the disk: the routes look the requested name up in `Assets.files`, a
read-only dict keyed by the exact hashed name. Nothing is ever joined to a folder at request
time. The OFL texts ship next to the fonts (LICENCE_TEXTS) and are never served.

Standard library only (tests/unit/test_web_never_reads_the_token.py).
"""

import hashlib
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

STATIC_DIR = Path(__file__).parent / "static"
STATIC_PREFIX = "/panel/static/"
HASH_CHARS = 12
# What is served. The order does not matter: load_assets() handles every CSS after the rest.
SERVED = (
    "logo.svg",
    "grain.svg",
    "InterVariable.woff2",
    "GeistMono-Variable.woff2",
    "fonts.css",
    "tokens.css",
    "panel.css",
    "login.js",
    "panel.js",
    "session.js",  # 11d-b: the expiry warning
    "try.js",  # 11d-b: «Prueba un texto»
)
# Shipped next to the fonts (the OFL asks for it), never served.
LICENCE_TEXTS = ("Inter-OFL.txt", "GeistMono-OFL.txt")
CONTENT_TYPES: Mapping[str, str] = MappingProxyType(
    {
        ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".woff2": "font/woff2",
        ".svg": "image/svg+xml",
    }
)
CSS_AT_RULES = frozenset({"font-face", "media", "container", "keyframes"})
SVG_NS = "http://www.w3.org/2000/svg"
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
SVG_ELEMENTS = frozenset(
    {
        "svg",
        "title",
        "defs",
        "g",
        "symbol",
        "use",
        "linearGradient",
        "stop",
        "rect",
        "path",
        "filter",
        "feTurbulence",
    }
)
SVG_ATTRIBUTES = frozenset(
    {
        "viewBox",
        "width",
        "height",
        "role",
        "aria-label",
        "aria-hidden",
        "id",
        "x",
        "y",
        "x1",
        "y1",
        "x2",
        "y2",
        "rx",
        "d",
        "transform",
        "offset",
        "stop-color",
        "fill",
        "fill-rule",
        "stroke",
        "stroke-opacity",
        "filter",
        "type",
        "baseFrequency",
        "numOctaves",
        "stitchTiles",
        "href",
        XLINK_HREF,
    }
)
_TEXT = (".css", ".js", ".svg")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\.(?:css|js|woff2|svg)")
_URL = re.compile(r"url\(\s*([\"']?)([^\"'()\s]+)\1\s*\)", re.IGNORECASE)
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_SVG_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_AT_RULE = re.compile(r"@([A-Za-z-]+)")
_FRAGMENT = re.compile(r"#[A-Za-z][A-Za-z0-9_-]*")
_FRAGMENT_URL = re.compile(r"url\(#[A-Za-z][A-Za-z0-9_-]*\)")


class AssetError(ValueError):
    """A static file that must not be served: the gateway refuses to start."""


@dataclass(frozen=True)
class Asset:
    body: bytes = field(repr=False)
    content_type: str


@dataclass(frozen=True)
class Assets:
    """The manifest: hashed name -> file, logical name -> URL, and the set of public paths."""

    files: Mapping[str, Asset]
    urls: Mapping[str, str]
    paths: frozenset[str]

    def get(self, name: str) -> Asset | None:
        """The file published under exactly this hashed name, or None."""
        return self.files.get(name)


def hashed_name(name: str, body: bytes) -> str:
    stem, dot, extension = name.rpartition(".")
    digest = hashlib.sha256(body).hexdigest()[:HASH_CHARS]
    return f"{stem}.{digest}{dot}{extension}"


def _read(folder: Path, name: str) -> bytes:
    if not _NAME.fullmatch(name):
        raise AssetError(f"not a static file name: {name!r}")
    path = folder / name
    if path.is_symlink():
        raise AssetError(f"{name}: a symlink is never served")
    if path.resolve().parent != folder.resolve():
        raise AssetError(f"{name}: outside the static folder")
    if not path.is_file():
        raise AssetError(f"{name}: no such file")
    return path.read_bytes()


def _text(name: str, body: bytes) -> str:
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        raise AssetError(f"{name}: not UTF-8") from None


def _svg(name: str, text: str) -> str:
    """The SVG without comments, if every element and attribute is on the allow-list."""
    text = _SVG_COMMENT.sub("", text)
    lowered = text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise AssetError(f"{name}: no DOCTYPE or ENTITY in an SVG")
    if "<?" in re.sub(r"\A\s*<\?xml\s[^>]*\?>", "", text):
        raise AssetError(f"{name}: no processing instruction in an SVG")
    try:
        root = ET.fromstring(text)  # noqa: S314 - no DOCTYPE, so no entity can be declared
    except ET.ParseError:
        raise AssetError(f"{name}: not well-formed XML") from None
    for element in root.iter():
        namespace, _, tag = str(element.tag).rpartition("}")
        if namespace != "{" + SVG_NS or tag not in SVG_ELEMENTS:
            raise AssetError(f"{name}: element {tag!r} is not allowed")
        for attribute, value in element.attrib.items():
            if attribute not in SVG_ATTRIBUTES:
                raise AssetError(f"{name}: attribute {attribute!r} is not allowed")
            if attribute in ("href", XLINK_HREF) and not _FRAGMENT.fullmatch(value):
                raise AssetError(f"{name}: a link may only be a fragment (#id)")
            if "url(" in value.lower() and not _FRAGMENT_URL.fullmatch(value.strip()):
                raise AssetError(f"{name}: url() may only point to a fragment (#id)")
    return text


def _css(name: str, text: str, published: Mapping[str, str]) -> str:
    """The CSS without comments and with url() rewritten, if it passes the allow-list."""
    text = _CSS_COMMENT.sub("", text)
    lowered = text.lower()
    if "\\" in text:
        raise AssetError(f"{name}: no backslash (CSS escapes) in a served CSS")
    if "image-set(" in lowered or "data:" in lowered:
        raise AssetError(f"{name}: image-set() and data: are not allowed")
    for rule in _AT_RULE.findall(text):
        if rule.lower() not in CSS_AT_RULES:
            raise AssetError(f"{name}: @{rule} is not allowed")
    if lowered.count("url(") != len(_URL.findall(text)):
        raise AssetError(f"{name}: a url() that cannot be checked")

    def rewrite(match: re.Match[str]) -> str:
        target = match.group(2)
        if target not in published or target.endswith(".css"):
            raise AssetError(f"{name}: url({target!r}) is not a listed file")
        return f'url("{published[target]}")'

    return _URL.sub(rewrite, text)


def load_assets(folder: Path | None = None, names: Iterable[str] | None = None) -> Assets:
    """Read, check, strip and hash every file of `names` (SERVED) in `folder` (STATIC_DIR), once.

    The defaults are read when called, not when this module loads."""
    folder = STATIC_DIR if folder is None else folder
    names = list(SERVED if names is None else names)
    if len(set(names)) != len(names):
        raise AssetError("a static file is listed twice")
    raw = {name: _read(folder, name) for name in names}
    published: dict[str, str] = {}  # logical name -> hashed name
    files: dict[str, Asset] = {}
    for name in sorted(names, key=lambda n: n.endswith(".css")):  # every CSS last
        body = raw[name]
        extension = name[name.rindex(".") :]
        if extension in _TEXT:
            text = _text(name, body)
            if extension == ".svg":
                body = _svg(name, text).encode("utf-8")
            elif extension == ".css":
                body = _css(name, text, published).encode("utf-8")
        published[name] = hashed_name(name, body)
        files[published[name]] = Asset(body, CONTENT_TYPES[extension])
    urls = {name: STATIC_PREFIX + published[name] for name in names}
    return Assets(
        files=MappingProxyType(files),
        urls=MappingProxyType(urls),
        paths=frozenset(urls.values()),
    )
