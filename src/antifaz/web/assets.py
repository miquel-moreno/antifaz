"""The panel's static files (ADR-0018, PR 11c-1): a fixed list, read once, served from memory.

At startup load_assets() reads each file of SERVED from static/ and publishes it as
name.<first 12 hex of its SHA-256>.ext under /panel/static/. A new version gets a new name, so a
browser may keep a file for a year (Cache-Control: immutable, decided by the door) and never
sees a stale one. Inside a CSS, every url("x") is rewritten to the hashed name of x, which must
be another file of the list (and not a CSS); the CSS is hashed after that, so it changes when a
file it points to changes.

Refused at startup (the gateway does not start): a name that is not one plain file name with
one of the four extensions; a symlink, or a file that resolves outside static/; a CSS with
@import, data:, or a url() that is not exactly a listed file (no scheme, no "//", no path, no
"#"); an SVG with a script, an event handler, foreignObject, javascript: or a link that is not a
fragment; a text file that is not UTF-8.

Serving never touches the disk: the routes look the requested name up in `Assets.files`, a
read-only dict keyed by the exact hashed name. Nothing is ever joined to a folder at request
time. The OFL texts ship next to the fonts (LICENCE_TEXTS) and are never served.

Standard library only (tests/unit/test_web_never_reads_the_token.py).
"""

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

STATIC_DIR = Path(__file__).parent / "static"
STATIC_PREFIX = "/panel/static/"
HASH_CHARS = 12
# Order matters only for the CSS: the files a CSS points to come first.
SERVED = (
    "logo.svg",
    "grain.svg",
    "InterVariable.woff2",
    "GeistMono-Variable.woff2",
    "fonts.css",
    "tokens.css",
    "panel.css",
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
_TEXT = (".css", ".js", ".svg")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\.(?:css|js|woff2|svg)")
_URL = re.compile(r"url\(\s*([\"']?)([^\"'()\s]+)\1\s*\)", re.IGNORECASE)
_SVG_FORBIDDEN = re.compile(
    r"<\s*script|<\s*foreignobject|javascript:|\son[a-z]+\s*=|href\s*=\s*[\"']?(?!#)",
    re.IGNORECASE,
)


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


def _check_svg(name: str, text: str) -> None:
    if _SVG_FORBIDDEN.search(text):
        raise AssetError(f"{name}: an SVG may not run or fetch anything")


def _rewrite_css(name: str, text: str, published: Mapping[str, str]) -> str:
    lowered = text.lower()
    if "@import" in lowered or "data:" in lowered:
        raise AssetError(f"{name}: @import and data: are not allowed")
    if lowered.count("url(") != len(_URL.findall(text)):
        raise AssetError(f"{name}: a url() that cannot be checked")

    def rewrite(match: re.Match[str]) -> str:
        target = match.group(2)
        if target not in published or target.endswith(".css"):
            raise AssetError(f"{name}: url({target!r}) is not a listed file")
        return f'url("{published[target]}")'

    return _URL.sub(rewrite, text)


def load_assets(folder: Path = STATIC_DIR, names: Iterable[str] = SERVED) -> Assets:
    """Read, check and hash every file of `names` in `folder`, once (see the module docstring)."""
    names = list(names)
    if len(set(names)) != len(names):
        raise AssetError("a static file is listed twice")
    raw = {name: _read(folder, name) for name in names}
    published: dict[str, str] = {}  # logical name -> hashed name
    files: dict[str, Asset] = {}
    for name in sorted(names, key=lambda n: n.endswith(".css")):  # the CSS last
        body = raw[name]
        extension = name[name.rindex(".") :]
        if extension in _TEXT:
            text = _text(name, body)
            if extension == ".svg":
                _check_svg(name, text)
            elif extension == ".css":
                body = _rewrite_css(name, text, published).encode("utf-8")
        published[name] = hashed_name(name, body)
        files[published[name]] = Asset(body, CONTENT_TYPES[extension])
    urls = {name: STATIC_PREFIX + published[name] for name in names}
    return Assets(
        files=MappingProxyType(files),
        urls=MappingProxyType(urls),
        paths=frozenset(urls.values()),
    )
