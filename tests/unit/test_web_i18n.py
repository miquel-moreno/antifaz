"""The panel's languages (issue 53, PR 11c-2b): web/i18n.py and the catalogs in web/i18n/.

- A catalog is a flat JSON object, key -> plain text. en.json is the reference: every other
  catalog has exactly its keys and, per key, the same {placeholders}. No HTML, no empty text,
  and each one says its own name under `language.name`.
- At startup every file whose name is a language code (`es.json`, `pt-BR.json`) is loaded; any
  other file is ignored. A broken catalog stops the startup with one clear line.
- Adding a language is adding a file: the parity tests below run over every catalog found.
- The language of a request: `?lang=xx` if it is a loaded catalog (the raw value is never used),
  else Accept-Language (q-values, primary subtag; only the first characters and the first ranges
  are looked at), else English.
"""

import json
from pathlib import Path

import pytest

import antifaz.web
from antifaz.web.i18n import (
    CATALOG_FILE,
    I18N_DIR,
    LANGUAGE_NAME,
    MAX_ACCEPT_LANGUAGE,
    MAX_LANGUAGE_RANGES,
    REFERENCE,
    CatalogError,
    load_catalogs,
    negotiate,
    placeholders,
)

WEB = Path(antifaz.web.__file__).parent
CATALOG_FILES = sorted(path for path in I18N_DIR.iterdir() if CATALOG_FILE.fullmatch(path.name))
REFERENCE_TEXTS: dict[str, str] = json.loads((I18N_DIR / "en.json").read_text("utf-8"))
AVAILABLE = ("en", "es")


def _write(folder: Path, name: str, texts: object) -> None:
    (folder / name).write_text(json.dumps(texts, ensure_ascii=False), encoding="utf-8")


def _folder(tmp_path: Path, **catalogs: object) -> Path:
    for code, texts in catalogs.items():
        _write(tmp_path, f"{code.replace('_', '-')}.json", texts)
    return tmp_path


EN = {LANGUAGE_NAME: "English", "hello": "Hello", "wait": "Wait {seconds} s"}
ES = {LANGUAGE_NAME: "Español", "hello": "Hola", "wait": "Espera {seconds} s"}


# --- the catalogs that ship -------------------------------------------------------------------


def test_the_catalogs_live_in_the_package_and_english_is_the_reference() -> None:
    assert I18N_DIR == WEB / "i18n"
    assert REFERENCE == "en"
    assert {path.stem for path in CATALOG_FILES} >= {"en", "es"}


def test_every_file_in_the_folder_is_a_catalog() -> None:
    """Nothing else ships next to them (the wheel test checks the same list)."""
    assert sorted(I18N_DIR.iterdir()) == CATALOG_FILES


@pytest.mark.parametrize("path", CATALOG_FILES, ids=lambda path: path.name)
def test_each_catalog_has_the_reference_keys_and_placeholders(path: Path) -> None:
    texts: dict[str, str] = json.loads(path.read_text("utf-8"))

    assert set(texts) == set(REFERENCE_TEXTS)
    for key, text in texts.items():
        assert placeholders(text) == placeholders(REFERENCE_TEXTS[key]), key
        assert text.strip(), key
        assert "<" not in text and ">" not in text, key
    assert texts[LANGUAGE_NAME].strip()


@pytest.mark.parametrize("path", CATALOG_FILES, ids=lambda path: path.name)
def test_each_catalog_is_utf8_without_bom_and_with_lf(path: Path) -> None:
    data = path.read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf") and b"\r" not in data
    data.decode("utf-8")


def test_the_shipped_catalogs_load() -> None:
    catalogs = load_catalogs()

    assert catalogs.codes[0] == "en"
    assert set(catalogs.codes) == {path.stem for path in CATALOG_FILES}
    assert catalogs["es"].name == "Español"
    assert catalogs["en"].name == "English"


# --- the loader -------------------------------------------------------------------------------


def test_a_valid_folder_loads_with_the_reference_first(tmp_path: Path) -> None:
    catalogs = load_catalogs(_folder(tmp_path, es=ES, en=EN))

    assert catalogs.codes == ("en", "es")
    assert catalogs["es"].text("hello") == "Hola"
    assert catalogs["es"].text("wait", seconds=7) == "Espera 7 s"
    assert catalogs["es"].text("wait") == "Espera {seconds} s"  # left for the script to fill


def test_a_region_catalog_loads(tmp_path: Path) -> None:
    catalogs = load_catalogs(_folder(tmp_path, en=EN, pt_BR={**ES, LANGUAGE_NAME: "Português"}))

    assert catalogs.codes == ("en", "pt-BR")


@pytest.mark.parametrize(
    "name", ["README.md", "ES.json", "es.JSON", "es_ES.json", "spanish.json", "e.json", "es.json~"]
)
def test_a_file_that_is_not_named_like_a_language_is_ignored(tmp_path: Path, name: str) -> None:
    folder = _folder(tmp_path, en=EN)
    (folder / name).write_text("not a catalog {", encoding="utf-8")

    assert load_catalogs(folder).codes == ("en",)


@pytest.mark.parametrize(
    ("texts", "fragment"),
    [
        ({LANGUAGE_NAME: "Español", "hello": "Hola"}, "missing"),
        ({**ES, "extra": "Más"}, "not in en.json"),
        ({**ES, "wait": "Espera {segundos} s"}, "placeholder"),
        ({**ES, "wait": "Espera s"}, "placeholder"),
        ({**ES, "hello": "<b>Hola</b>"}, "plain text"),
        ({**ES, "hello": "Hola &amp; adiós"}, "plain text"),
        ({**ES, "hello": "   "}, "empty"),
        ({**ES, "hello": ""}, "empty"),
        ({**ES, "hello": 3}, "text"),
        ({**ES, "hello": "Hola {"}, "brace"),
        ({**ES, "hello": "Hola\nadiós"}, "control"),
        ({**ES, LANGUAGE_NAME: " "}, "empty"),
        (["not", "an", "object"], "object"),
    ],
)
def test_a_broken_catalog_stops_the_startup_with_a_clear_message(
    tmp_path: Path, texts: object, fragment: str
) -> None:
    folder = _folder(tmp_path, en=EN, es=texts)

    with pytest.raises(CatalogError) as raised:
        load_catalogs(folder)
    message = str(raised.value)
    assert "es.json" in message and fragment in message
    assert "\n" not in message
    # no chained traceback: nothing of the parser's error is printed with it
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None or raised.value.__suppress_context__


def test_a_catalog_without_its_language_name_stops_the_startup(tmp_path: Path) -> None:
    folder = _folder(tmp_path, en={"hello": "Hello"})

    with pytest.raises(CatalogError, match=LANGUAGE_NAME):
        load_catalogs(folder)


def test_a_repeated_key_stops_the_startup(tmp_path: Path) -> None:
    folder = _folder(tmp_path, en=EN)
    (folder / "es.json").write_text(
        '{"language.name": "Español", "hello": "Hola", "hello": "Ola", "wait": "{seconds}"}',
        encoding="utf-8",
    )

    with pytest.raises(CatalogError, match="repeated"):
        load_catalogs(folder)


@pytest.mark.parametrize("content", [b"{", b"\xff\xfe", b'\xef\xbb\xbf{"a": "b"}'])
def test_a_file_that_is_not_utf8_json_stops_the_startup(tmp_path: Path, content: bytes) -> None:
    folder = _folder(tmp_path, en=EN)
    (folder / "es.json").write_bytes(content)

    with pytest.raises(CatalogError, match=r"es\.json"):
        load_catalogs(folder)


def test_a_bad_key_name_stops_the_startup(tmp_path: Path) -> None:
    with pytest.raises(CatalogError, match="key"):
        load_catalogs(_folder(tmp_path, en={**EN, "Bad Key": "x"}))


def test_without_the_reference_nothing_starts(tmp_path: Path) -> None:
    with pytest.raises(CatalogError, match=r"en\.json"):
        load_catalogs(_folder(tmp_path, es=ES))


def test_an_unknown_key_or_value_is_an_error_not_an_empty_text(tmp_path: Path) -> None:
    catalog = load_catalogs(_folder(tmp_path, en=EN))["en"]

    with pytest.raises(CatalogError):
        catalog.text("nope")
    with pytest.raises(CatalogError):
        catalog.text("hello", seconds=3)


def test_the_catalogs_are_read_only(tmp_path: Path) -> None:
    catalogs = load_catalogs(_folder(tmp_path, en=EN))

    with pytest.raises(TypeError):
        catalogs["en"].texts["hello"] = "x"  # type: ignore[index]


def test_a_huge_catalog_is_refused(tmp_path: Path) -> None:
    big = {**EN, **{f"k{n}": "x" * 100 for n in range(2000)}}
    with pytest.raises(CatalogError, match="big"):
        load_catalogs(_folder(tmp_path, en=big))


# --- the language of a request ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "header", "expected"),
    [
        (None, None, "en"),
        (None, "", "en"),
        ("es", None, "es"),
        ("en", "es", "en"),  # ?lang wins over the header
        ("fr", "es", "es"),  # an unknown ?lang is ignored, never reflected
        ("<script>", "es", "es"),
        ("ES", None, "en"),  # exact codes only
        ("es ", None, "en"),
        (None, "es", "es"),
        (None, "es-ES", "es"),
        (None, "es-ES,es;q=0.9,en;q=0.8", "es"),
        (None, "fr", "en"),
        (None, "fr-FR,fr;q=0.9", "en"),
        (None, "fr,es;q=0.5", "es"),
        (None, "en;q=0.2,es;q=0.8", "es"),
        (None, "es;q=0,en", "en"),  # q=0 means "not this one"
        (None, "es;q=0", "en"),
        (None, "ES-es", "es"),
        (None, "*", "en"),
        (None, "es;q=2", "en"),  # an invalid q: the range is skipped
        (None, "es;q=abc,en;q=0.1", "en"),
        (None, "<script>alert(1)</script>", "en"),
        (None, ";;;,,,===", "en"),
        (None, "es-", "en"),
        (None, "e" * 9, "en"),
        (None, "\x00es", "en"),
        (None, "en-US,en;q=0.9", "en"),
    ],
)
def test_the_language_of_a_request(query: str | None, header: str | None, expected: str) -> None:
    assert negotiate(query, header, AVAILABLE) == expected


def test_a_region_catalog_matches_its_exact_tag_first() -> None:
    available = ("en", "pt", "pt-BR")

    assert negotiate(None, "pt-BR", available) == "pt-BR"
    assert negotiate(None, "pt-PT", available) == "pt"
    assert negotiate(None, "pt-br", available) == "pt-BR"


def test_ties_keep_the_order_of_the_header() -> None:
    assert negotiate(None, "es,en", AVAILABLE) == "es"
    assert negotiate(None, "en,es", AVAILABLE) == "en"


def test_only_the_first_ranges_are_looked_at() -> None:
    assert MAX_LANGUAGE_RANGES == 10
    nine = ",".join(["fr"] * (MAX_LANGUAGE_RANGES - 1))

    assert negotiate(None, f"{nine},es", AVAILABLE) == "es"  # the 10th range counts
    assert negotiate(None, f"{nine},fr,es", AVAILABLE) == "en"  # the 11th does not


def test_only_the_first_characters_are_looked_at() -> None:
    assert MAX_ACCEPT_LANGUAGE == 256
    padding = "x" * (MAX_ACCEPT_LANGUAGE - 1)

    assert negotiate(None, padding + ",es", AVAILABLE) == "en"
    assert negotiate(None, "es," + "y" * 100_000, AVAILABLE) == "es"
    # a range cut by the limit is dropped, not read as a shorter one
    cut = "fr," + "z" * (MAX_ACCEPT_LANGUAGE - 5) + ",es;q=0.1"
    assert negotiate(None, cut, AVAILABLE) == "en"


def test_a_huge_header_does_not_scan_everything() -> None:
    """A million ranges cost the same as ten: nothing past the limits is split or parsed."""
    header = "fr," * 1_000_000 + "es"

    assert negotiate(None, header, AVAILABLE) == "en"


# --- review of 11c-2b: RFC 9110 details --------------------------------------------------------


def test_empty_list_elements_do_not_use_up_the_entry_limit() -> None:
    """RFC 9110 5.6.1: empty elements of a list are ignored. They are dropped before the limit of
    ranges, so commas alone never push a real range out (the size limit still applies)."""
    commas = ", " * 30  # 60 characters, no range at all
    assert negotiate(None, commas + "es", AVAILABLE) == "es"
    assert negotiate(None, ",,,, ,\t, es", AVAILABLE) == "es"
    ten_real = ",".join(["fr"] * MAX_LANGUAGE_RANGES)
    assert negotiate(None, ten_real + ",,es", AVAILABLE) == "en"  # still only ten ranges
    assert negotiate(None, "," * 300 + "es", AVAILABLE) == "en"  # past the size limit


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("es;q=0.5, *", "en"),  # the wildcard (q=1) is any language: the reference
        ("es, *;q=0.5", "es"),
        ("*;q=0.5, es;q=0.4", "en"),
        ("es, *", "es"),  # a tie keeps the header's order
        ("fr, *;q=0.1", "en"),
        ("*;q=0", "en"),
        ("es;q=0, *", "en"),
    ],
)
def test_the_wildcard_stands_for_the_reference_at_its_weight(header: str, expected: str) -> None:
    assert negotiate(None, header, AVAILABLE) == expected
