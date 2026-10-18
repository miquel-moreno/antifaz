"""The panel's languages (issue 53, PR 11c-2b): catalogs read once at startup, and the language
of each request. Standard library only (tests/unit/test_web_never_reads_the_token.py).

Catalogs: web/i18n/<code>.json, a flat JSON object of key -> plain text. Only files named like a
language code are read (CATALOG_FILE: `es.json`, `pt-BR.json`); anything else in the folder is
ignored. en.json is the reference. Every catalog must have exactly its keys, the same
{placeholders} per key, plain text (no `<`, `>`, `&...;`, control characters or stray braces),
no empty text and its own name under `language.name`. Any problem raises CatalogError with one
line that names the file and the key (never a traceback chain): the gateway does not start.
Adding a language is adding a file; no code changes.

The language of a request (negotiate): `?lang=<code>` if it is exactly a loaded catalog (the raw
value is never used or reflected), else Accept-Language, else English. Of Accept-Language only
the first MAX_ACCEPT_LANGUAGE characters and the first MAX_LANGUAGE_RANGES ranges are looked at
(a range cut by the limit is dropped); q-values are honoured (q=0 means "not this one"), ties keep
the header's order, and a range matches a catalog by its exact tag or by its primary subtag.
"""

import json
import re
from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

I18N_DIR = Path(__file__).parent / "i18n"
REFERENCE = "en"
LANGUAGE_NAME = "language.name"
CATALOG_FILE = re.compile(r"[a-z]{2}(?:-[A-Z]{2})?\.json")
MAX_CATALOG_BYTES = 64 * 1024
MAX_ACCEPT_LANGUAGE = 256
MAX_LANGUAGE_RANGES = 10

_KEY = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*")
_PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}")
_NOT_PLAIN = re.compile(r"[<>]|&#?[A-Za-z0-9]+;")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_RANGE = re.compile(r"([A-Za-z]{1,8})((?:-[A-Za-z0-9]{1,8})*)")
_QUALITY = re.compile(r"[qQ]=(0(?:\.[0-9]{0,3})?|1(?:\.0{0,3})?)")


class CatalogError(ValueError):
    """A catalog that cannot be used: the gateway refuses to start."""


def placeholders(text: str) -> frozenset[str]:
    """The {names} a text expects."""
    return frozenset(_PLACEHOLDER.findall(text))


@dataclass(frozen=True)
class Catalog:
    code: str
    texts: Mapping[str, str] = field(repr=False)

    @property
    def name(self) -> str:
        """The language's own name ("Español", "English")."""
        return self.texts[LANGUAGE_NAME]

    def text(self, key: str, **values: object) -> str:
        """The text of `key` with the given {placeholders} filled; the others stay as they are
        (a script fills them). An unknown key or value is an error, never an empty text."""
        raw = self.texts.get(key)
        if raw is None:
            raise CatalogError(f"i18n/{self.code}.json: no text for key {key!r}")
        unknown = set(values) - placeholders(raw)
        if unknown:
            raise CatalogError(f"i18n/{self.code}.json: key {key!r} has no {sorted(unknown)}")
        return _PLACEHOLDER.sub(lambda match: str(values.get(match.group(1), match.group(0))), raw)


@dataclass(frozen=True)
class Catalogs:
    """Every loaded catalog, the reference first and then by code."""

    by_code: Mapping[str, Catalog]

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(self.by_code)

    def __getitem__(self, code: str) -> Catalog:
        return self.by_code[code]

    def __iter__(self) -> Iterator[Catalog]:
        return iter(self.by_code.values())

    def negotiate(self, query: str | None, accept_language: str | None) -> str:
        return negotiate(query, accept_language, self.codes)


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise CatalogError("repeated key")
    return dict(pairs)


def _read(path: Path) -> dict[str, str]:
    """One catalog file as key -> text, if it is a flat object of plain texts."""
    where = f"i18n/{path.name}"
    data = path.read_bytes()
    if len(data) > MAX_CATALOG_BYTES:
        raise CatalogError(f"{where}: too big (more than {MAX_CATALOG_BYTES} bytes)")
    try:
        parsed = json.loads(data.decode("utf-8"), object_pairs_hook=_unique)
    except CatalogError as error:
        raise CatalogError(f"{where}: {error}") from None
    except (UnicodeDecodeError, ValueError):
        raise CatalogError(f"{where}: not a UTF-8 JSON file (without BOM)") from None
    if not isinstance(parsed, dict):
        raise CatalogError(f"{where}: must be one JSON object of key -> text")
    texts: dict[str, str] = {}
    for key, text in parsed.items():
        if not _KEY.fullmatch(key):
            raise CatalogError(f"{where}: bad key name {key!r} (lowercase words with dots)")
        if not isinstance(text, str):
            raise CatalogError(f"{where}: key {key!r} must be a text")
        if not text.strip():
            raise CatalogError(f"{where}: key {key!r} is empty")
        if _NOT_PLAIN.search(text):
            raise CatalogError(f"{where}: key {key!r} must be plain text (no HTML)")
        if _CONTROL.search(text):
            raise CatalogError(f"{where}: key {key!r} has a control character")
        if "{" in _PLACEHOLDER.sub("", text) or "}" in _PLACEHOLDER.sub("", text):
            raise CatalogError(f"{where}: key {key!r} has a brace that is not a {{placeholder}}")
        texts[key] = text
    if LANGUAGE_NAME not in texts:
        raise CatalogError(f"{where}: key {LANGUAGE_NAME!r} (the language's own name) is missing")
    return texts


def _check_against(name: str, texts: Mapping[str, str], reference: Mapping[str, str]) -> None:
    where = f"i18n/{name}"
    missing = sorted(set(reference) - set(texts))
    if missing:
        raise CatalogError(f"{where}: key {missing[0]!r} is missing (compared with en.json)")
    extra = sorted(set(texts) - set(reference))
    if extra:
        raise CatalogError(f"{where}: key {extra[0]!r} is not in en.json")
    for key, text in texts.items():
        if placeholders(text) != placeholders(reference[key]):
            expected = sorted(placeholders(reference[key]))
            raise CatalogError(f"{where}: key {key!r} needs the placeholder(s) {expected}")


def load_catalogs(folder: Path | None = None) -> Catalogs:
    """Read and check every catalog of `folder` (I18N_DIR), once; the default is read now."""
    folder = I18N_DIR if folder is None else folder
    paths = sorted(path for path in folder.iterdir() if CATALOG_FILE.fullmatch(path.name))
    found = {path.name.removesuffix(".json"): path for path in paths if path.is_file()}
    if REFERENCE not in found:
        raise CatalogError(f"i18n/{REFERENCE}.json, the reference catalog, is missing")
    reference = _read(found[REFERENCE])
    catalogs = {REFERENCE: Catalog(REFERENCE, MappingProxyType(reference))}
    for code in sorted(found):
        if code == REFERENCE:
            continue
        texts = _read(found[code])
        _check_against(found[code].name, texts, reference)
        catalogs[code] = Catalog(code, MappingProxyType(texts))
    return Catalogs(MappingProxyType(catalogs))


def _ranges(header: str) -> list[tuple[float, int, str]]:
    """(q, position, tag) of the ranges looked at, without q=0 and without invalid ones."""
    text = header[:MAX_ACCEPT_LANGUAGE]
    parts = text.split(",", MAX_LANGUAGE_RANGES)
    if len(parts) > MAX_LANGUAGE_RANGES:
        parts = parts[:MAX_LANGUAGE_RANGES]  # the cut is in what is left out
    elif len(header) > MAX_ACCEPT_LANGUAGE:
        parts = parts[:-1]  # the last range may be cut in the middle
    found: list[tuple[float, int, str]] = []
    for position, part in enumerate(parts):
        tag, *parameters = (piece.strip(" \t") for piece in part.split(";"))
        if not _RANGE.fullmatch(tag):
            continue
        quality = 1.0
        valid = True
        for parameter in parameters:
            match = _QUALITY.fullmatch(parameter)
            if match is None:
                valid = False
                break
            quality = float(match.group(1))
        if valid and quality > 0:
            found.append((quality, position, tag))
    return found


def _candidates(tag: str) -> list[str]:
    primary, *rest = tag.split("-")
    primary = primary.lower()
    if rest and len(rest[0]) == 2 and rest[0].isalpha():
        return [f"{primary}-{rest[0].upper()}", primary]
    return [primary]


def negotiate(query: str | None, accept_language: str | None, available: Collection[str]) -> str:
    """The language of a request: one of `available`, or the reference."""
    if query is not None and query in available:
        return query
    if accept_language:
        ranked = sorted(_ranges(accept_language), key=lambda item: (-item[0], item[1]))
        for _, _, tag in ranked:
            for candidate in _candidates(tag):
                if candidate in available:
                    return candidate
    return REFERENCE
