"""The «Estado» view's markup (issue 53, PR 11c-3b, ADR-0018 and its note of 2026-10-19).

Rendered from the real templates and catalogs, per language:

- The side line: a `role=status` line (empty at first: nothing was checked yet) and the
  «Actualizar» button; an error callout with `role=alert`, hidden.
- Four cards in the grid (hidden until the first answer): the gateway (version, uptime, how the
  panel is served, the three security counts and the fixed note that sessions live in memory per
  process), the NER (its five states, none marked yet), the providers (OpenAI and Anthropic, with
  the `antifaz doctor --providers` line) and the evidence placeholder.
- What the approved design had and this view must not: «Escucha en», «Base de datos»,
  /healthz, latency meters, «Comprobar ahora». No header pill, no dot on the tab, no counters.
- A `<template id="status-messages">` with every label the script needs, from the catalog:
  each enum value, "unknown", the loading and checked lines, the errors and the uptime units.
"""

import re
from html.parser import HTMLParser

import pytest

from antifaz import __version__
from antifaz.web.assets import load_assets
from antifaz.web.i18n import load_catalogs
from antifaz.web.status import NER_STATES
from antifaz.web.templating import Pages

CATALOGS = load_catalogs()
PAGES = Pages(load_assets(), version=__version__)
MESSAGES = (
    *(f"overall.{value}" for value in ("ok", "warn", "danger", "stale")),
    *(f"ner.{value}" for value in sorted(NER_STATES - {"unknown"})),
    "scheme.https",
    "scheme.http_localhost",
    "address.default",
    "address.custom",
    "key.yes",
    "key.no",
    "unknown",
    "checking",
    "checked",
    "checked.old",
    "error.network",
    "error.refused",
    "uptime.days",
    "uptime.hours",
    "uptime.minutes",
    "uptime.less",
)


class _Tree(HTMLParser):
    """Each element: (tag, attributes, text directly inside, path of parent ids)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[dict[str, object]] = []
        self.stack: list[dict[str, object]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        element: dict[str, object] = {
            "tag": tag,
            "attrs": {name: value or "" for name, value in attrs},
            "text": "",
            "parents": [e["attrs"].get("id", "") for e in self.stack],  # type: ignore[union-attr]
        }
        self.elements.append(element)
        if tag not in ("meta", "link", "input", "br", "img", "use", "path"):
            self.stack.append(element)

    def handle_endtag(self, tag: str) -> None:
        if tag not in [e["tag"] for e in self.stack]:
            return  # a void or self-closed element (<use/>): never on the stack
        while self.stack:
            if self.stack.pop()["tag"] == tag:
                break

    def handle_data(self, data: str) -> None:
        if self.stack:
            self.stack[-1]["text"] = str(self.stack[-1]["text"]) + data


def _tree(lang: str) -> list[dict[str, object]]:
    parser = _Tree()
    parser.feed(PAGES.panel(lang, "csrf-x"))
    parser.close()
    return parser.elements


def _by_id(elements: list[dict[str, object]], id_: str) -> dict[str, object]:
    (found,) = [e for e in elements if e["attrs"].get("id") == id_]  # type: ignore[union-attr]
    return found


def _inside(elements: list[dict[str, object]], id_: str) -> list[dict[str, object]]:
    return [e for e in elements if id_ in e["parents"]]  # type: ignore[operator]


def _view_text(lang: str) -> str:
    page = PAGES.panel(lang, "csrf-x")
    start = page.index('id="panel-status"')
    return page[start : page.index("</section>", start)]


@pytest.mark.parametrize("lang", CATALOGS.codes)
def test_the_side_line_the_button_and_the_error_callout(lang: str) -> None:
    elements = _tree(lang)
    text = CATALOGS[lang].text

    line = _by_id(elements, "status-time")
    assert line["attrs"]["role"] == "status"  # type: ignore[index]
    assert str(line["text"]).strip() == ""
    button = _by_id(elements, "status-refresh")
    assert button["tag"] == "button"
    assert button["attrs"]["type"] == "button"  # type: ignore[index]
    words = "".join(str(e["text"]) for e in [button, *_inside(elements, "status-refresh")])
    assert text("status.refresh") in words
    alert = _by_id(elements, "status-alert")
    assert alert["attrs"]["role"] == "alert"  # type: ignore[index]
    assert "hidden" in alert["attrs"]  # type: ignore[operator]
    assert "panel-status" in alert["parents"]  # type: ignore[operator]


@pytest.mark.parametrize("lang", CATALOGS.codes)
def test_four_cards_with_what_was_decided(lang: str) -> None:
    elements = _tree(lang)
    text = CATALOGS[lang].text
    grid = _by_id(elements, "status-grid")
    assert "hidden" in grid["attrs"]  # type: ignore[operator]
    assert grid["attrs"]["aria-busy"] == "false"  # type: ignore[index]
    cards = [e for e in _inside(elements, "status-grid") if e["tag"] == "article"]
    assert len(cards) == 4
    view = _view_text(lang)
    for key in (
        "status.gateway",
        "status.version",
        "status.uptime",
        "status.panel_scheme",
        "status.allowed_hosts",
        "status.allowed_origins",
        "status.trusted_proxies",
        "status.sessions",
        "status.ner",
        "status.providers",
        "status.provider.openai",
        "status.provider.anthropic",
        "status.evidence",
        "status.evidence.soon",
        "view.status.command",
        "status.foot",
    ):
        assert text(key) in view, key
    for value in ("ok", "starting", "circuit_open", "closed", "off"):
        assert len([e for e in elements if e["attrs"].get("data-ner") == value]) == 1  # type: ignore[union-attr]
    assert not [e for e in elements if "aria-current" in e["attrs"] and "data-ner" in e["attrs"]]  # type: ignore[operator]
    providers = [e["attrs"].get("data-provider") for e in elements if "data-provider" in e["attrs"]]  # type: ignore[union-attr,operator]
    assert providers == ["openai", "anthropic"]


@pytest.mark.parametrize("lang", CATALOGS.codes)
def test_nothing_the_review_removed(lang: str) -> None:
    view = _view_text(lang)
    page = PAGES.panel(lang, "csrf-x")

    for gone in ("/healthz", "meter", "Escucha en", "Listens on", "Base de datos", "Database"):
        assert gone not in view, gone
    for gone in ("Comprobar ahora", "Check now", "live-pill", "app-status"):
        assert gone not in page, gone
    start = page.index('id="tab-status"')
    tab = page[start : page.index("</button>", start)]
    assert "dot" not in tab  # no warning dot on the tab


@pytest.mark.parametrize("lang", CATALOGS.codes)
def test_the_messages_template_holds_every_label_the_script_needs(lang: str) -> None:
    elements = _tree(lang)
    template = _by_id(elements, "status-messages")
    assert template["tag"] == "template"
    inside = {
        e["attrs"].get("data-msg"): str(e["text"])  # type: ignore[union-attr]
        for e in _inside(elements, "status-messages")
    }
    text = CATALOGS[lang].text

    assert set(inside) == set(MESSAGES)
    assert inside["unknown"] == text("status.unknown")
    assert inside["ner.circuit_open"] == text("status.ner.circuit_open")
    assert "{time}" in inside["checked"] and "{time}" in inside["checked.old"]
    assert "{days}" in inside["uptime.days"] and "{hours}" in inside["uptime.days"]
    assert all(value.strip() for value in inside.values())


@pytest.mark.parametrize("lang", CATALOGS.codes)
def test_the_ner_states_show_no_technical_code(lang: str) -> None:
    """Miquel's review: only the approved labels and descriptions, never ok/starting/... codes."""
    texts = [
        str(e["text"])
        for e in _tree(lang)
        if any(str(p).startswith("panel-status") for p in e["parents"])  # type: ignore[union-attr]
    ]
    for code in ("circuit_open", "starting", "closed"):
        assert not any(code in text for text in texts), code


def test_the_loading_dim_keeps_the_text_contrast() -> None:
    """While it reads, the cards may dim, but never below opacity .8 (AA text stays AA)."""
    from antifaz.web.assets import STATIC_DIR

    css = (STATIC_DIR / "panel.css").read_text("utf-8")
    rules = re.findall(r'\.sgrid\[aria-busy="true"\]\{([^}]*)\}', css)
    assert rules  # the rule exists
    for rule in rules:
        for value in re.findall(r"opacity:\s*([0-9.]+)", rule):
            assert float(value) >= 0.8, rule


def test_the_old_empty_status_texts_are_gone() -> None:
    for code in CATALOGS.codes:
        assert "view.status.empty" not in CATALOGS[code].texts
