"""The «Prueba un texto» screen's markup (issue 53, PR 11d-b, ADR-0018).

Rendered from the real templates and catalogs, per language, with the NER on (the words a Try
may give it, TRY_NER_MAX_WORDS) and off:

- The field: a textarea WITHOUT a name (a form without script sends nothing of it), with
  autocomplete, spell check, capitalisation, autocorrection and writing suggestions off (the
  browser keeps no copy and sends nothing to a spelling service); the form posts to the fixed
  /panel with no query, and a <noscript> note says why nothing happens without JavaScript.
- The limits come from the server: the bytes (MAX_TEXT_BYTES) and, with the NER on, the words
  (TRY_NER_MAX_WORDS) in data attributes, and that number in the too-long callout's text. The
  number is never written in a template, a script or a catalog (a test reads them).
- The live region (role=status) for the counts, the three panes as keyboard-scrollable regions
  with a name (the whole text, never cut), a label for every entity type, the callouts (error,
  too long for the name detector, names not checked) and the blocked note.
- The session (session.js): two metas with the seconds left, a polite status container for the
  warning (never an alert: it does not interrupt) and its template.
- What the prototype had and this screen must not: the policy selector (rrhh), the timing, the
  model, a 504, the broken chain, the mask animation and the header status pill.
- The example texts: only data the scanner finds WITHOUT the NER (DNI, email, IBAN and phone),
  never a name; the English one with a +34 phone.
"""

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from antifaz import EntityType, __version__
from antifaz.api.app import TRY_NER_MAX_WORDS
from antifaz.detect.scan import scan
from antifaz.web.assets import STATIC_DIR, load_assets
from antifaz.web.i18n import I18N_DIR, load_catalogs
from antifaz.web.templating import TEMPLATES, TEMPLATES_DIR, Pages
from antifaz.web.try_text import MAX_TEXT_BYTES

CATALOGS = load_catalogs()
PAGES = Pages(load_assets(), version=__version__)
LANGS = CATALOGS.codes


class _Tree(HTMLParser):
    """Every element: its tag and attributes, and the text inside it (children included)."""

    VOID = ("meta", "link", "input", "br", "img", "use", "path", "source")

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[dict[str, object]] = []
        self.stack: list[dict[str, object]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        element: dict[str, object] = {
            "tag": tag,
            "attrs": {name: value or "" for name, value in attrs},
            "text": "",
            "inside": [str(e["tag"]) for e in self.stack],
            "ids": [e["attrs"].get("id", "") for e in self.stack],  # type: ignore[union-attr]
        }
        self.elements.append(element)
        if tag not in self.VOID:
            self.stack.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if self.stack and self.stack[-1]["tag"] == tag:
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        if tag not in [e["tag"] for e in self.stack]:
            return
        while self.stack:
            if self.stack.pop()["tag"] == tag:
                break

    def handle_data(self, data: str) -> None:
        for element in self.stack:
            element["text"] = str(element["text"]) + data


def _page(lang: str, words: int | None = TRY_NER_MAX_WORDS) -> str:
    return PAGES.panel(
        lang, "csrf-x", idle_seconds=1800, max_seconds=28800, ner_words=words,
        max_text_bytes=MAX_TEXT_BYTES,
    )  # fmt: skip


def _tree(page: str) -> _Tree:
    tree = _Tree()
    tree.feed(page)
    tree.close()
    return tree


def _by_id(tree: _Tree, element_id: str) -> dict[str, object]:
    found = [e for e in tree.elements if e["attrs"].get("id") == element_id]  # type: ignore[union-attr]
    assert len(found) == 1, element_id
    return found[0]


def _attrs(tree: _Tree, element_id: str) -> dict[str, str]:
    return _by_id(tree, element_id)["attrs"]  # type: ignore[return-value]


# --- the field and the form ------------------------------------------------------------------


@pytest.mark.parametrize("lang", LANGS)
def test_the_field_has_no_name_and_keeps_no_copy(lang: str) -> None:
    field = _attrs(_tree(_page(lang)), "try-text")

    assert _by_id(_tree(_page(lang)), "try-text")["tag"] == "textarea"
    assert "name" not in field
    assert field["autocomplete"] == "off"
    assert field["spellcheck"] == "false"
    assert field["autocapitalize"] == "off"
    assert field["autocorrect"] == "off"
    assert field["writingsuggestions"] == "false"
    assert field["aria-keyshortcuts"] == "Control+Enter Meta+Enter"
    assert "value" not in field


@pytest.mark.parametrize("lang", LANGS)
def test_the_form_posts_to_the_fixed_panel_path_without_a_query(lang: str) -> None:
    tree = _tree(_page(lang))
    form = _attrs(tree, "try-form")

    assert form["method"] == "post"
    assert form["action"] == "/panel"
    assert "novalidate" in form
    named = [e for e in tree.elements if "try-form" in e["ids"] and "name" in e["attrs"]]  # type: ignore[operator]
    assert named == []  # nothing of the form has a name: without script it sends nothing


@pytest.mark.parametrize("lang", LANGS)
def test_a_noscript_note_says_why(lang: str) -> None:
    tree = _tree(_page(lang))
    notes = [e for e in tree.elements if e["tag"] == "noscript"]

    assert len(notes) == 1
    assert CATALOGS[lang].text("try.noscript") in str(notes[0]["text"])


@pytest.mark.parametrize("lang", LANGS)
def test_the_live_region_is_polite_and_empty(lang: str) -> None:
    live = _by_id(_tree(_page(lang)), "try-live")

    assert live["attrs"]["role"] == "status"  # type: ignore[index]
    assert live["attrs"]["aria-live"] == "polite"  # type: ignore[index]
    assert str(live["text"]).strip() == ""


# --- the limits come from the server ---------------------------------------------------------


@pytest.mark.parametrize("lang", LANGS)
def test_with_the_ner_the_page_shows_exactly_the_server_word_limit(lang: str) -> None:
    tree = _tree(_page(lang))
    form = _attrs(tree, "try-form")

    assert form["data-max-words"] == str(TRY_NER_MAX_WORDS)
    assert form["data-max-bytes"] == str(MAX_TEXT_BYTES)
    assert form["data-ner-detector"] == "on"
    body = CATALOGS[lang].text("try.too_long.body", max=TRY_NER_MAX_WORDS)
    assert body in str(_by_id(tree, "try-too-long")["text"])
    assert f" {TRY_NER_MAX_WORDS} " in body
    assert "hidden" in _attrs(tree, "try-too-long")


@pytest.mark.parametrize("words", [TRY_NER_MAX_WORDS, 123])
def test_the_word_limit_follows_the_server_value(words: int) -> None:
    tree = _tree(_page("es", words))

    assert _attrs(tree, "try-form")["data-max-words"] == str(words)
    assert f" {words} " in str(_by_id(tree, "try-too-long")["text"])


@pytest.mark.parametrize("lang", LANGS)
def test_without_the_ner_there_is_no_word_limit_nor_its_callout(lang: str) -> None:
    page = _page(lang, None)
    tree = _tree(page)
    form = _attrs(tree, "try-form")

    assert "data-max-words" not in form
    assert form["data-ner-detector"] == "off"
    assert not [e for e in tree.elements if e["attrs"].get("id") == "try-too-long"]  # type: ignore[union-attr]
    assert form["data-max-bytes"] == str(MAX_TEXT_BYTES)


def _sources() -> list[Path]:
    return [
        *(TEMPLATES_DIR / name for name in TEMPLATES),
        *sorted(STATIC_DIR.glob("*.js")),
        *sorted(I18N_DIR.glob("*.json")),
    ]


def test_the_word_limit_is_never_written_in_a_template_script_or_catalog() -> None:
    number = re.compile(rf"(?<![0-9]){TRY_NER_MAX_WORDS}(?![0-9])")
    for path in _sources():
        assert not number.search(path.read_text("utf-8")), path.name
    assert len(_sources()) >= 8  # the four templates, the scripts and both catalogs


def test_the_byte_limit_is_never_written_in_a_template_script_or_catalog() -> None:
    for path in _sources():
        text = path.read_text("utf-8").replace(" ", "").replace(".", "").replace(",", "")
        assert str(MAX_TEXT_BYTES) not in text, path.name


# --- the result: whole texts in scrollable regions, a label per type ------------------------


@pytest.mark.parametrize("lang", LANGS)
def test_the_three_panes_are_named_keyboard_scrollable_regions(lang: str) -> None:
    tree = _tree(_page(lang))
    ids = {str(e["attrs"].get("id")) for e in tree.elements}  # type: ignore[union-attr]
    for pane in ("try-input", "try-sent", "try-reply"):
        attrs = _attrs(tree, pane)
        assert "ptext" in attrs["class"].split()
        assert attrs["tabindex"] == "0"
        assert attrs["role"] == "region"
        assert attrs["aria-labelledby"] in ids


@pytest.mark.parametrize("lang", LANGS)
def test_every_entity_type_has_a_label(lang: str) -> None:
    tree = _tree(_page(lang))
    labels = {
        str(e["attrs"]["data-type"]): str(e["text"]).strip()  # type: ignore[index]
        for e in tree.elements
        if "try-types" in e["ids"] and "data-type" in e["attrs"]  # type: ignore[operator]
    }

    assert set(labels) == {entity.value.lower() for entity in EntityType}
    assert all(labels.values())


@pytest.mark.parametrize("lang", LANGS)
def test_without_a_script_the_buttons_are_off(lang: str) -> None:
    """Privacy review of 11d-b: try.js turns them on; without it nothing can be sent at all."""
    tree = _tree(_page(lang))

    assert "disabled" in _attrs(tree, "try-submit")
    assert "disabled" in _attrs(tree, "try-example")


@pytest.mark.parametrize("lang", LANGS)
def test_the_8_hours_come_from_the_session_store(lang: str) -> None:
    from antifaz.web.sessions import MAX_AGE_SECONDS

    tree = _tree(_page(lang))
    messages = {
        str(e["attrs"]["data-msg"]): str(e["text"])  # type: ignore[index]
        for e in tree.elements
        if "session-messages" in e["ids"] and e["attrs"].get("data-msg")  # type: ignore[operator, union-attr]
    }
    hours = MAX_AGE_SECONDS // 3600

    assert f" {hours} " in messages["max"] and "{time}" in messages["max"]
    assert "{hours}" in CATALOGS[lang].texts["session.warn.max"]


@pytest.mark.parametrize("lang", LANGS)
def test_the_callouts_and_the_blocked_note_start_hidden(lang: str) -> None:
    tree = _tree(_page(lang))
    for element_id in ("try-error", "try-names", "try-result", "try-blocked", "try-example-note"):
        assert "hidden" in _attrs(tree, element_id), element_id
    assert _attrs(tree, "try-error")["role"] == "alert"
    assert "hidden" not in _attrs(tree, "try-empty")


@pytest.mark.parametrize("lang", LANGS)
def test_the_names_not_checked_phrase_is_one_catalog_key(lang: str) -> None:
    """The callout's title and the meta line use the same text (try.names.title)."""
    tree = _tree(_page(lang))
    phrase = CATALOGS[lang].text("try.names.title")

    assert phrase in str(_by_id(tree, "try-names")["text"])
    messages = [
        e
        for e in tree.elements
        if "try-messages" in e["ids"] and e["attrs"].get("data-msg")  # type: ignore[operator, union-attr]
    ]
    by_key = {str(e["attrs"]["data-msg"]): str(e["text"]) for e in messages}  # type: ignore[index]
    assert by_key["names.title"] == phrase


# --- the session -----------------------------------------------------------------------------


@pytest.mark.parametrize("lang", LANGS)
def test_the_session_warning_is_polite_and_never_an_alert(lang: str) -> None:
    tree = _tree(_page(lang))
    box = _by_id(tree, "session-warning")

    assert box["attrs"]["role"] == "status"  # type: ignore[index]
    assert "aria-live" not in box["attrs"] or box["attrs"]["aria-live"] == "polite"  # type: ignore[index, operator]
    assert str(box["text"]).strip() == ""  # filled only when the warning shows
    assert "main" in box["inside"]  # type: ignore[operator]
    banner = _by_id(tree, "session-banner")
    assert banner["tag"] == "template"
    assert CATALOGS[lang].text("session.warn.title") in str(banner["text"])
    assert CATALOGS[lang].text("session.warn.continue") in str(banner["text"])


@pytest.mark.parametrize("lang", LANGS)
def test_the_page_loads_the_three_scripts_in_order(lang: str) -> None:
    urls = load_assets().urls
    scripts = re.findall(r'<script src="([^"]+)" defer></script>', _page(lang))

    assert scripts == [urls["panel.js"], urls["session.js"], urls["try.js"]]


# --- what the prototype had and this screen must not -----------------------------------------


@pytest.mark.parametrize("lang", LANGS)
def test_nothing_that_depends_on_future_work(lang: str) -> None:
    page = _page(lang)

    for absent in (
        "rrhh",
        "pg-policy",
        'class="seg',
        "journey",
        "mask-anim",
        "anim-replay",
        "app-status",
        "504",
        "claude-",
        "special_category",
    ):
        assert absent not in page, absent


# --- the example texts -----------------------------------------------------------------------


@pytest.mark.parametrize("lang", LANGS)
def test_the_example_holds_only_data_found_without_the_ner(lang: str) -> None:
    example = CATALOGS[lang].text("try.example.text")

    found = {span.type for span in scan(example)}

    assert found == {EntityType.ES_DNI, EntityType.EMAIL, EntityType.IBAN, EntityType.PHONE}
    assert EntityType.PERSON not in found


def test_the_english_example_has_a_spanish_international_phone() -> None:
    example = CATALOGS["en"].text("try.example.text")
    phones = [example[s.start : s.end] for s in scan(example) if s.type == EntityType.PHONE]

    assert len(phones) == 1 and phones[0].startswith("+34")


@pytest.mark.parametrize("lang", LANGS)
def test_the_example_reaches_the_page_only_through_its_template(lang: str) -> None:
    tree = _tree(_page(lang))
    holder = [
        e
        for e in tree.elements
        if "try-messages" in e["ids"] and e["attrs"].get("data-msg") == "example"  # type: ignore[operator, union-attr]
    ]

    assert len(holder) == 1
    assert str(holder[0]["text"]) == CATALOGS[lang].text("try.example.text")


# --- sizes that grow with the text -----------------------------------------------------------


def test_the_counter_badges_are_sized_in_rem() -> None:
    css = (STATIC_DIR / "panel.css").read_text("utf-8")
    for selector in (r"\.num\{", r"\.found dd b\{"):
        for rule in re.findall(rf"(?:^|[}}\s]){selector}([^}}]*)\}}", css):
            for prop in ("width", "height", "min-width"):
                value = re.search(rf"(?<![-\w]){prop}:([^;]*)", rule)
                if value:
                    assert "px" not in value.group(1), (selector, prop, value.group(1))
