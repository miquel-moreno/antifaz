"""The «En directo» screen's markup (issue 53, PR 11e-2, ADR-0018).

Rendered from the real templates and catalogs, per language. live.js fills it from the SSE of
GET /panel/api/live with textContent and createElement only; the page holds every word it needs:

- The header: the pill (a polite status with a dot and a word: «Conectando…», «Conectado» or
  «Desconectado»; never a date or «desde el arranque», owner change) and «Volver a conectar»,
  hidden until a connection is lost, refused or closed.
- The callouts, hidden: the connection lost, too many views open (busy), closed after 30
  minutes (max_age), and several processes (info: «estos son los datos de este proceso»).
- The figures, hidden until the first snapshot: «Datos ocultados desde el arranque» with bars by
  type, «Bloqueos» with the reasons, «Peticiones a la IA» with its parts and the rule of what it
  counts (owner condition: only the proxy, blocks included; tries and scans apart), the evidence
  chain placeholder and the uptime note (each process counts its own).
- The feed: «Últimas peticiones», newest first, a log (role=log, polite, additions) with its
  empty state, and the foot (nobody watching, nothing is prepared; watching never extends the
  session).
- A <template id="live-messages"> with every label the script needs: each client, provider,
  reason, outcome tag, plural forms, the separator and the generic label for an unknown value.
- No «Llega en una próxima versión» block, no date in the pill, no «IA local» card (no routing to
  a local model yet).
"""

from html.parser import HTMLParser

import pytest

from antifaz import __version__
from antifaz.live import Client, Reason
from antifaz.web.assets import load_assets
from antifaz.web.i18n import load_catalogs
from antifaz.web.live_stream import MAX_PER_SESSION, MAX_STREAMS
from antifaz.web.templating import Pages

CATALOGS = load_catalogs()
PAGES = Pages(load_assets(), version=__version__)
LANGS = CATALOGS.codes
REASONS = [reason.value for reason in Reason]
MESSAGES = {
    "pill.connecting",
    "pill.connected",
    "pill.disconnected",
    "processes",
    "uptime",
    "uptime.days",
    "uptime.hours",
    "uptime.minutes",
    "uptime.less",
    *(f"client.{client.value}" for client in Client),
    "who.try",
    "provider.openai",
    "provider.anthropic",
    "provider.unknown",
    *(f"reason.{reason}" for reason in REASONS),
    *(f"blocks.{reason}" for reason in REASONS),
    *(f"blocks.{reason}_one" for reason in REASONS),
    "blocks.zero",
    "tag.masked",
    "tag.blocked",
    "tag.try",
    "tag.scan",
    "tag.no_data",
    "tag.upstream_error",
    "tag.unknown",
    "what.none",
    "what.not_added",
    "what.type",
    "type.unknown",
    "requests.masked",
    "requests.no_data",
    "requests.blocked",
    "requests.blocked_one",
    "requests.upstream_error",
    "requests.upstream_error_one",
    "requests.zero",
    "apart",
    "tries",
    "tries_one",
    "scans",
    "scans_one",
    "sep",
}
PLACEHOLDERS = {
    "processes": {"{n}"},
    "uptime": {"{uptime}"},
    "tag.masked": {"{n}"},
    "what.type": {"{type}", "{n}"},
    "requests.masked": {"{n}"},
    "requests.no_data": {"{n}"},
    "requests.blocked": {"{n}"},
    "requests.upstream_error": {"{n}"},
    "apart": {"{tries}", "{scans}"},
    "tries": {"{n}"},
    "scans": {"{n}"},
    **{f"blocks.{reason}": {"{n}"} for reason in REASONS},
}


class _Tree(HTMLParser):
    """Every element: its tag, attributes, the text inside it (children included) and the ids of
    the elements around it."""

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
            "ids": [e["attrs"].get("id", "") for e in self.stack],  # type: ignore[union-attr]
        }
        self.elements.append(element)
        if tag not in self.VOID:
            self.stack.append(element)

    def handle_endtag(self, tag: str) -> None:
        if tag not in [e["tag"] for e in self.stack]:
            return
        while self.stack:
            if self.stack.pop()["tag"] == tag:
                break

    def handle_data(self, data: str) -> None:
        for element in self.stack:
            element["text"] = str(element["text"]) + data


def _page(lang: str) -> str:
    return PAGES.panel(lang, "csrf-x", live_per_session=MAX_PER_SESSION, live_total=MAX_STREAMS)


def _tree(lang: str) -> list[dict[str, object]]:
    parser = _Tree()
    parser.feed(_page(lang))
    parser.close()
    return parser.elements


def _by_id(tree: list[dict[str, object]], id_: str) -> dict[str, object]:
    (found,) = [e for e in tree if e["attrs"].get("id") == id_]  # type: ignore[union-attr]
    return found


def _attrs(element: dict[str, object]) -> dict[str, str]:
    return element["attrs"]  # type: ignore[return-value]


def _text(element: dict[str, object]) -> str:
    return " ".join(str(element["text"]).split())


def _view(lang: str) -> str:
    page = _page(lang)
    start = page.index('id="panel-live"')
    return page[start : page.index("</section>", start)]


@pytest.mark.parametrize("lang", LANGS)
def test_the_header_holds_the_pill_and_the_hidden_reconnect_button(lang: str) -> None:
    tree, text = _tree(lang), CATALOGS[lang].text
    pill = _by_id(tree, "live-pill")

    assert _attrs(pill)["role"] == "status"
    assert "live-pill" in _attrs(pill)["class"].split()
    assert "panel-live" in pill["ids"]  # type: ignore[operator]
    assert _text(pill) == text("live.pill.connecting")  # what it says while it opens
    button = _by_id(tree, "live-reconnect")
    assert button["tag"] == "button"
    assert _attrs(button)["type"] == "button"
    assert "hidden" in _attrs(button)
    assert _text(button) == text("live.reconnect")


@pytest.mark.parametrize("lang", LANGS)
def test_the_pill_never_says_a_date_or_since_start_up(lang: str) -> None:
    """Owner change: the pill says only the connection; «desde el arranque» goes with the
    numbers."""
    text = CATALOGS[lang].text
    for key in ("live.pill.connecting", "live.pill.connected", "live.pill.disconnected"):
        assert "·" not in text(key)
        assert "{" not in text(key)
    assert text("live.pill.connected") in ("Conectado", "Connected")
    assert ("arranque" in text("live.hidden")) or ("start-up" in text("live.hidden"))


@pytest.mark.parametrize(
    ("id_", "kind", "title", "body"),
    [
        ("live-lost", "warn", "live.lost.title", "live.lost.body"),
        ("live-busy", "warn", "live.busy.title", None),  # its body: the test below
        ("live-aged", "warn", "live.aged.title", "live.aged.body"),
        ("live-processes", "info", "live.processes.title", None),
    ],
)
@pytest.mark.parametrize("lang", LANGS)
def test_each_callout_is_hidden_and_says_what_was_approved(
    lang: str, id_: str, kind: str, title: str, body: str | None
) -> None:
    tree, text = _tree(lang), CATALOGS[lang].text
    callout = _by_id(tree, id_)

    assert "hidden" in _attrs(callout)
    assert _attrs(callout)["class"].split() == ["callout", kind]
    assert text(title) in _text(callout)
    if body is not None:
        assert text(body) in _text(callout)
    if kind == "warn":
        assert _attrs(callout)["role"] == "alert"


@pytest.mark.parametrize("lang", LANGS)
def test_the_busy_text_says_both_limits_from_the_server(lang: str) -> None:
    """Review 1: «busy» comes from either limit (per session or in total), and the numbers are
    the server's constants, rendered into the page: never written in a catalog or a script."""
    body = CATALOGS[lang].text("live.busy.body")
    assert "{per_session}" in body and "{total}" in body
    assert not any(char.isdigit() for char in body)
    shown = _text(_by_id(_tree(lang), "live-busy"))
    expected = CATALOGS[lang].text("live.busy.body", per_session=MAX_PER_SESSION, total=MAX_STREAMS)
    assert expected in shown
    assert "30" in CATALOGS[lang].text("live.aged.title")


@pytest.mark.parametrize("lang", LANGS)
def test_the_lost_callout_has_a_text_for_when_no_figure_ever_came(lang: str) -> None:
    """Review 1: lost before the first snapshot, there are no figures «from before»: live.js
    shows the other text."""
    tree, text = _tree(lang), CATALOGS[lang].text
    figures = _by_id(tree, "live-lost-figures")
    none = _by_id(tree, "live-lost-none")
    assert _text(figures) == text("live.lost.body")
    assert _text(none) == text("live.lost.none")
    assert "hidden" in _attrs(none) and "hidden" not in _attrs(figures)
    assert "live-lost" in none["ids"]  # type: ignore[operator]


@pytest.mark.parametrize("lang", LANGS)
def test_the_figures_are_hidden_until_the_first_snapshot(lang: str) -> None:
    tree, text = _tree(lang), CATALOGS[lang].text
    data = _by_id(tree, "live-data")
    assert "hidden" in _attrs(data)
    stats = _by_id(tree, "live-stats")
    articles = [e for e in tree if e["tag"] == "article" and "live-stats" in e["ids"]]  # type: ignore[operator]
    assert [_attrs(a)["class"] for a in articles] == [
        "stat s-prot",
        "stat s-block",
        "stat s-local",
        "stat s-chain",
    ]
    whole = _text(stats)
    for key in (
        "live.hidden",
        "live.hidden.none",
        "live.blocks",
        "live.requests",
        "live.requests.rule",
        "live.chain",
        "status.evidence.soon",
    ):
        assert text(key) in whole, key
    bars = _by_id(tree, "live-bars")
    assert bars["tag"] == "ul"
    assert _attrs(bars)["aria-label"] == text("live.hidden.by_type")
    for id_ in ("live-hidden", "live-blocks", "live-requests"):
        number = _by_id(tree, id_)
        assert "stat-num" in _attrs(number)["class"].split()
        assert _text(number) == ""
    for id_ in ("live-blocks-sub", "live-requests-sub", "live-apart", "live-uptime"):
        assert _text(_by_id(tree, id_)) == ""


@pytest.mark.parametrize("lang", LANGS)
def test_the_rule_of_what_requests_count_is_on_the_screen(lang: str) -> None:
    """Owner condition: «Peticiones» says WHAT it counts: the requests to the AI only, blocks
    included; tries and scans are apart and do not add (live.js fills the apart line)."""
    text = CATALOGS[lang].text
    rule = text("live.requests.rule").lower()
    assert ("bloqueadas" in rule) or ("blocked" in rule)
    assert text("live.requests.rule") in _view(lang)
    apart = text("live.apart").lower()
    assert ("no suman" in apart) or ("not added" in apart)


@pytest.mark.parametrize("lang", LANGS)
def test_the_feed_is_a_polite_log_with_its_empty_state_and_the_foot(lang: str) -> None:
    tree, text = _tree(lang), CATALOGS[lang].text
    log = _by_id(tree, "live-log")
    assert _attrs(log)["role"] == "log"
    assert _attrs(log)["aria-live"] == "polite"
    assert _attrs(log)["aria-relevant"] == "additions"
    assert _attrs(log)["aria-labelledby"] == "live-feed-t"
    assert _text(_by_id(tree, "live-feed-t")) == text("live.feed")
    feed = _by_id(tree, "live-feed")
    assert feed["tag"] == "ol"
    assert _text(feed) == ""
    empty = _by_id(tree, "live-empty")
    assert _text(empty) == text("live.empty")
    view = _view(lang)
    assert text("live.feed.order") in view
    assert text("live.foot") in view


@pytest.mark.parametrize("lang", LANGS)
def test_the_messages_template_holds_every_label_the_script_needs(lang: str) -> None:
    tree, text = _tree(lang), CATALOGS[lang].text
    template = _by_id(tree, "live-messages")
    assert template["tag"] == "template"
    inside = {
        _attrs(e)["data-msg"]: str(e["text"])
        for e in tree
        if "live-messages" in e["ids"] and "data-msg" in _attrs(e)  # type: ignore[operator]
    }

    assert set(inside) == MESSAGES
    assert all(value.strip() for key, value in inside.items() if key != "sep")
    assert inside["sep"].strip() == "·"
    for key, names in PLACEHOLDERS.items():
        for name in names:
            assert name in inside[key], (key, name)
    assert inside["who.try"] == text("tab.playground")
    assert inside["provider.openai"] == text("status.provider.openai")
    assert inside["tag.unknown"] == text("status.unknown")
    assert inside["uptime.days"] == text("status.uptime.days")
    assert inside["client.other"] in ("Otro", "Other")


@pytest.mark.parametrize("lang", LANGS)
def test_the_page_loads_the_four_scripts_in_order(lang: str) -> None:
    urls = load_assets().urls
    page = _page(lang)
    scripts = [part.split('"', 1)[0] for part in page.split('<script src="')[1:]]

    assert scripts == [urls["panel.js"], urls["session.js"], urls["try.js"], urls["live.js"]]


@pytest.mark.parametrize("lang", LANGS)
def test_nothing_that_depends_on_future_work(lang: str) -> None:
    view = _view(lang)
    text = CATALOGS[lang].text

    assert text("view.soon") not in view
    for gone in ("IA local", "Local AI", "Ollama", "data-only", "i-check", "chain-links"):
        assert gone not in view, gone
    assert "view.live.empty" not in CATALOGS[lang].texts  # the old empty block is gone
