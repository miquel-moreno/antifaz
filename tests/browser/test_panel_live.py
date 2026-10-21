"""The «En directo» screen in real browsers (issue 53, PR 11e-2, ADR-0018): Chromium, Firefox,
WebKit.

The gateway of these tests has an invented provider in its own process (tests/browser/server.py,
`fake_provider`): the live feed is driven through the real engine (the proxy, /antifaz/scan and
«Prueba un texto»), never a real provider. An init script records every EventSource the page
makes, so a test can say whether a stream is closed (readyState 2: the browser never reconnects
it) without waiting for a reconnection that should not come.

- When it connects: never at load nor on another view; exactly one stream when «En directo» is
  shown; closed on another view, when the page is hidden, after «Salir» and after a logout in
  another tab (the page then leaves for the login, like an expired session); reopened when the
  view is shown again or the page is visible again on it. Switching views back and forth never
  runs into the limit of two streams per session (the server frees each place at once).
- A stream cut by the server: «Desconectado», the warning, the figures kept and dimmed, the stream
  closed for good (no new request in a bounded window, longer than any browser's default
  reconnection delay), and «Volver a conectar» opens exactly one new stream (also by keyboard).
- The page's clock moved on 29 minutes, and then past the session's end: no other stream.
- The states: connecting, connected with data, empty, busy (too many views), closed after 30
  minutes (max_age, shortened here), several processes.
- The numbers add up (owner condition): requests = their parts (blocks included), blocks = their
  reasons, hidden data = its bars; tries and scans apart; the same after a fresh snapshot as when
  they arrived one by one.
- Data only from the closed schema: an unknown value gets a generic label, never shown raw.
- Privacy red team: the sentinel DNI through the proxy, «Prueba un texto» and /antifaz/scan never
  reaches the view, the title, an attribute or the console; nothing is stored.
- No CSP violation, a clean console, axe finds nothing in every state (light and dark, es and
  en), forced colours, 320 and 390 px without a horizontal scroll, text at 200 % without clipping.
"""

import re
import sys
import time
from collections.abc import Callable
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from antifaz.web.i18n import load_catalogs
from antifaz.web.live_stream import MAX_PER_SESSION, MAX_STREAMS
from tests.browser.console_rules import unexpected_console
from tests.browser.harness import TIMEOUT_MS, NewTab, Tab
from tests.browser.server import LIVE_PATH, PanelServer, ServerOptions
from tests.browser.test_panel_text_size import SETTLE_JS
from tests.browser.test_panel_try import log_in, try_text, wait_result
from tests.conftest import SENTINEL_DNI

pytestmark = pytest.mark.browser

CATALOGS = load_catalogs()
FAKE = ServerOptions(fake_provider=True)
OTHER_DNI = "87654321X"
ATTACHMENT = [
    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AA=="}}
]
# Chromium waits 3 s before an EventSource reconnects by itself, WebKit 3 s, Firefox 5 s: a
# window longer than all of them, measured by waiting for a request that must not come.
NO_RETRY_WINDOW_MS = 7_000

# Every EventSource the page makes, kept so a test can read its readyState.
STREAMS_RECORDER = """(() => {
  if (window.__antifazStreams) return;
  const seen = [];
  Object.defineProperty(window, '__antifazStreams', {value: seen});
  const Original = window.EventSource;
  window.EventSource = new Proxy(Original, {
    construct(target, args) {
      const es = Reflect.construct(target, args); seen.push(es); return es;
    }
  });
})();"""
STATES_JS = "() => window.__antifazStreams.map((es) => es.readyState)"
VISIBILITY_JS = """(state) => {
  Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => state});
  Object.defineProperty(document, 'hidden', {configurable: true, get: () => state === 'hidden'});
  document.dispatchEvent(new Event('visibilitychange'));
}"""
PILL_JS = "(state) => document.getElementById('live-pill').getAttribute('data-state') === state"
ROWS_JS = "(n) => document.querySelectorAll('#live-feed > li').length === n"
TEXT_JS = "(id) => document.getElementById(id).textContent"
SHOWN_JS = """(id) => { const el = document.getElementById(id);
  return !el.hidden && !el.closest('[hidden]') && el.getClientRects().length > 0; }"""
BARS_JS = """() => [...document.querySelectorAll('#live-bars > li')].map((li) => ({
  label: li.firstElementChild.textContent, n: li.querySelector('.n').textContent,
  v: li.querySelector('.meter > i').getAttribute('data-v')}))"""
ROWS_TEXT_JS = """() => [...document.querySelectorAll('#live-feed > li')].map((li) => ({
  kind: li.getAttribute('data-kind'), time: li.querySelector('time').textContent,
  who: li.querySelector('.ev-who').textContent, what: li.querySelector('.ev-what').textContent,
  tag: li.querySelector('.tag').textContent}))"""
IN_VIEW_JS = """(needle) => {
  const view = document.getElementById('panel-live');
  return view.outerHTML.includes(needle) || document.title.includes(needle);
}"""
STORAGE_JS = """async () => ({
  local: localStorage.length,
  session: sessionStorage.length,
  databases: indexedDB.databases ? (await indexedDB.databases()).length : 0,
  caches: self.caches ? (await caches.keys()).length : 0,
})"""
PAGE_OVERFLOW_JS = "() => document.documentElement.scrollWidth - window.innerWidth"
CLIPPED_JS = """() => [...document.querySelectorAll('#panel-live *')].filter((el) =>
  [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim() !== '') &&
  el.getClientRects().length > 0 && (
    (getComputedStyle(el).overflowX !== 'visible' && el.scrollWidth > el.clientWidth + 1) ||
    el.getBoundingClientRect().right > window.innerWidth + 1)).map((el) =>
  el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + ' ' +
  el.textContent.trim().slice(0, 30))"""
ACTIVE_ID_JS = "() => document.activeElement ? document.activeElement.id : null"
NUMBER = re.compile(r"\d[\d.,\s]*")  # a grouping space may be a no-break one


def _locale(lang: str) -> str:
    return "es-ES" if lang == "es" else "en-US"


def _text(lang: str, key: str) -> str:
    return CATALOGS[lang].text(key)


def _is_live(url: str) -> bool:
    return urlsplit(url).path == LIVE_PATH


def _live_requests(tab: Tab) -> int:
    return len([url for url in tab.requests if _is_live(url)])


def _tab(new_tab: NewTab, lang: str = "en", **options: object) -> Tab:
    tab = new_tab(locale=_locale(lang), **options)  # type: ignore[arg-type]
    tab.context.add_init_script(STREAMS_RECORDER)
    return tab


def _streams(tab: Tab) -> list[int]:
    states: list[int] = tab.page.evaluate(STATES_JS)
    return states


def _pill(tab: Tab, state: str) -> None:
    tab.page.wait_for_function(PILL_JS, arg=state, timeout=TIMEOUT_MS)


def _open_live(tab: Tab, state: str = "connected") -> None:
    with tab.page.expect_request(lambda request: _is_live(request.url)):
        tab.page.click("#tab-live")
    _pill(tab, state)


def _shown(tab: Tab, element_id: str) -> bool:
    value: bool = tab.page.evaluate(SHOWN_JS, element_id)
    return value


def _content(tab: Tab, element_id: str) -> str:
    value: str = tab.page.evaluate(TEXT_JS, element_id)
    return value


def _number(text: str) -> int:
    found = NUMBER.search(text)
    assert found, text
    return int(re.sub(r"\D", "", found.group(0)))


def _parts(text: str) -> list[int]:
    return [_number(part) for part in text.split("·") if part.strip()]


def _rows(tab: Tab, count: int) -> None:
    tab.page.wait_for_function(ROWS_JS, arg=count, timeout=TIMEOUT_MS)


def _eventually(check: Callable[[], bool], what: str) -> None:
    """A condition of the server, polled until true or the hang guard (never a fixed wait)."""
    deadline = time.monotonic() + TIMEOUT_MS / 1000
    while not check():
        assert time.monotonic() < deadline, what
        time.sleep(0.02)


def _no_live_request_within(tab: Tab, window_ms: int) -> bool:
    """True when no request to the live URL starts during `window_ms`."""
    try:
        with tab.page.expect_request(lambda r: _is_live(r.url), timeout=window_ms):
            pass
    except PlaywrightTimeoutError:
        return True
    return False


def _at_the_login(tab: Tab, server: PanelServer) -> None:
    """The page went to the login, once, and it loaded. The stream's `end: logout` can reach
    live.js (and so session.js) before panel.js gets the logout's answer: the page must still
    leave only once (a second navigation would abort the first)."""
    tab.page.wait_for_url(f"{server.origin}/panel/login*", wait_until="load")
    tab.page.locator("#login-token").wait_for()


def _clean(tab: Tab, browser_name: str, *, cut: int = 0) -> None:
    """No CSP violation, page error or foreign request, and a clean console: only the line a
    browser logs for a stream the test cut on purpose (`cut`, console_rules.py) may be there.
    A stream the page closed itself is a failed request only as the engine's own abort."""
    assert tab.csp_violations() == []
    found = unexpected_console(
        tab.console, browser_name, sys.platform, tab.console_locations, cut=cut
    )
    assert found == []
    assert tab.errors == []
    assert tab.blocked == []
    assert tab.foreign_requests() == []
    assert tab.unexpected_failures(cut=cut) == []


def drive_feed(server: PanelServer) -> None:
    """The scenario of the numbers: 2 with data, 2 without, 2 blocked (an attachment and a body
    nested too deep), 1 provider error and 1 scan. Hidden: 3 DNI and 1 email."""
    deep: object = "x"
    for _ in range(150):
        deep = [deep]
    assert (
        server.proxy_openai(f"DNI {SENTINEL_DNI}, ana@example.com", user_agent="claude-cli/2.0")
        == 200
    )
    assert (
        server.proxy_anthropic(f"{OTHER_DNI} y {SENTINEL_DNI}", user_agent="Anthropic/Python 1")
        == 200
    )
    assert server.proxy_openai("hola") == 200
    assert server.proxy_openai("sin nada", user_agent="codex_cli_rs/0.1") == 200
    assert server.proxy_anthropic(ATTACHMENT) == 400
    assert server.proxy_openai("hola", extra={"metadata": {"a": deep}}) == 400
    assert server.proxy_openai("hola", model="fail") == 502
    assert server.scan(f"Mi DNI {SENTINEL_DNI}") == 200


def _figures(tab: Tab) -> dict[str, object]:
    return {
        "hidden": _number(_content(tab, "live-hidden")),
        "bars": tab.page.evaluate(BARS_JS),
        "blocks": _number(_content(tab, "live-blocks")),
        "blocks_sub": _content(tab, "live-blocks-sub"),
        "requests": _number(_content(tab, "live-requests")),
        "requests_sub": _content(tab, "live-requests-sub"),
        "apart": _content(tab, "live-apart"),
    }


def _assert_adds_up(figures: dict[str, object]) -> None:
    bars = figures["bars"]
    assert isinstance(bars, list)
    assert figures["hidden"] == sum(_number(bar["n"]) for bar in bars)
    assert figures["blocks"] == sum(_parts(str(figures["blocks_sub"])))
    assert figures["requests"] == sum(_parts(str(figures["requests_sub"])))


# --- when it connects -------------------------------------------------------------------------


@pytest.mark.parametrize("server_options", [FAKE])
def test_nothing_at_load_nor_on_other_views_and_one_stream_when_shown(
    new_tab: NewTab, server: PanelServer, browser_name: str
) -> None:
    tab = _tab(new_tab)
    log_in(tab, server, "en")
    for other in ("#tab-keys", "#tab-policies", "#tab-status", "#tab-playground"):
        tab.page.click(other)
    assert _live_requests(tab) == 0

    _open_live(tab)
    tab.page.click("#tab-live")  # selected again: nothing new
    assert _live_requests(tab) == 1
    assert _streams(tab) == [1]  # OPEN
    assert _content(tab, "live-pill") == _text("en", "live.pill.connected")
    _clean(tab, browser_name)


@pytest.mark.parametrize("server_options", [FAKE])
def test_another_view_or_a_hidden_page_closes_it_and_coming_back_reopens_it(
    new_tab: NewTab, server: PanelServer, browser_name: str
) -> None:
    tab = _tab(new_tab)
    log_in(tab, server, "en")
    _open_live(tab)

    for round_ in range(3):  # never runs into the two streams a session may hold
        tab.page.click("#tab-keys")
        assert _streams(tab)[-1] == 2  # CLOSED
        _eventually(lambda: server.live_streams_open() == 0, "the server freed the place")
        _open_live(tab)
        assert _live_requests(tab) == round_ + 2

    tab.page.evaluate(VISIBILITY_JS, "hidden")
    assert _streams(tab)[-1] == 2
    _eventually(lambda: server.live_streams_open() == 0, "hidden: the server freed the place")
    assert _live_requests(tab) == 4
    with tab.page.expect_request(lambda request: _is_live(request.url)):
        tab.page.evaluate(VISIBILITY_JS, "visible")
    _pill(tab, "connected")
    assert _live_requests(tab) == 5

    tab.page.click("#tab-status")
    tab.page.evaluate(VISIBILITY_JS, "hidden")
    tab.page.evaluate(VISIBILITY_JS, "visible")  # visible again, but on another view
    assert _live_requests(tab) == 5
    assert _streams(tab) == [2, 2, 2, 2, 2]
    _clean(tab, browser_name)


@pytest.mark.parametrize("server_options", [FAKE])
def test_a_cut_stream_shows_disconnected_never_retries_and_reconnects_on_the_button(
    new_tab: NewTab, server: PanelServer, browser_name: str
) -> None:
    tab = _tab(new_tab, "es")
    log_in(tab, server, "es")
    _open_live(tab)
    assert server.proxy_openai(f"DNI {SENTINEL_DNI}") == 200
    _rows(tab, 1)

    server.cut_live_streams()
    assert server.scan("hola") == 200  # its frame is the one that breaks the stream
    _pill(tab, "off")
    assert _shown(tab, "live-lost")
    assert _content(tab, "live-pill") == _text("es", "live.pill.disconnected")
    assert _shown(tab, "live-reconnect")
    assert _shown(tab, "live-data")  # the figures from before, kept
    assert tab.page.locator("#live-data").get_attribute("class") == "live-data is-stale"
    assert _streams(tab) == [2]  # closed: the browser will not reconnect it by itself
    assert _no_live_request_within(tab, NO_RETRY_WINDOW_MS)
    assert _live_requests(tab) == 1

    server.restore_live_streams()  # the next stream is a normal one
    with tab.page.expect_request(lambda request: _is_live(request.url)):
        tab.page.click("#live-reconnect")
    _pill(tab, "connected")
    assert _live_requests(tab) == 2
    assert not _shown(tab, "live-lost")
    assert not _shown(tab, "live-reconnect")
    assert tab.page.locator("#live-data").get_attribute("class") == "live-data"
    assert tab.page.evaluate(ACTIVE_ID_JS) == "panel-live"  # the focus is not lost
    _rows(tab, 2)  # the fresh snapshot holds both
    _clean(tab, browser_name, cut=1)


@pytest.mark.parametrize("server_options", [FAKE])
def test_reconnect_works_from_the_keyboard(new_tab: NewTab, server: PanelServer) -> None:
    tab = _tab(new_tab)
    log_in(tab, server, "en")
    tab.page.locator("#tab-playground").focus()
    with tab.page.expect_request(lambda request: _is_live(request.url)):
        tab.page.keyboard.press("2")
    _pill(tab, "connected")
    server.cut_live_streams()
    assert server.scan("hola") == 200
    _pill(tab, "off")
    server.restore_live_streams()

    tab.page.locator("#panel-live").focus()
    tab.page.keyboard.press("Tab")
    assert tab.page.evaluate(ACTIVE_ID_JS) == "live-reconnect"
    with tab.page.expect_request(lambda request: _is_live(request.url)):
        tab.page.keyboard.press("Enter")
    _pill(tab, "connected")
    assert _live_requests(tab) == 2


@pytest.mark.parametrize("server_options", [FAKE])
def test_the_page_clock_moved_on_makes_no_other_stream(
    new_tab: NewTab, server: PanelServer, browser_name: str
) -> None:
    tab = _tab(new_tab)
    tab.page.clock.install()
    log_in(tab, server, "en")
    _open_live(tab)

    tab.page.clock.run_for(29 * 60 * 1000)  # before the session's end (session.js)
    tab.page.evaluate(SETTLE_JS)
    assert _live_requests(tab) == 1
    assert _streams(tab) == [1]

    tab.page.clock.run_for(2 * 60 * 1000)  # past it: session.js leaves for the login
    _at_the_login(tab, server)
    assert _live_requests(tab) == 1
    _eventually(lambda: server.live_streams_open() == 0, "the page left: the place is free")


@pytest.mark.parametrize("server_options", [FAKE])
def test_logout_ends_the_view(new_tab: NewTab, server: PanelServer, browser_name: str) -> None:
    tab = _tab(new_tab)
    log_in(tab, server, "en")
    _open_live(tab)

    tab.page.click(".app-sidebar-foot [data-logout]")
    _at_the_login(tab, server)
    assert _live_requests(tab) == 1
    _eventually(lambda: server.live_streams_open() == 0, "the stream ended with the session")
    _clean(tab, browser_name)


@pytest.mark.parametrize("server_options", [FAKE])
def test_a_logout_in_another_tab_sends_this_one_to_the_login(
    new_tab: NewTab, server: PanelServer, browser_name: str
) -> None:
    tab = _tab(new_tab)
    log_in(tab, server, "en")
    _open_live(tab)
    other = new_tab(locale="en-US")
    tab.share_session_with(other)
    other.goto("/panel?lang=en")

    other.page.click(".app-sidebar-foot [data-logout]")
    _at_the_login(other, server)
    _at_the_login(tab, server)  # `end: logout`, then session.js
    assert _live_requests(tab) == 1
    _eventually(lambda: server.live_streams_open() == 0, "no stream is left")
    _clean(tab, browser_name)


# --- the states -------------------------------------------------------------------------------


@pytest.mark.parametrize("server_options", [FAKE])
def test_connecting_says_so_and_shows_no_figures_yet(new_tab: NewTab, server: PanelServer) -> None:
    tab = _tab(new_tab, "es")
    log_in(tab, server, "es")
    tab.page.route(f"{tab.origin}{LIVE_PATH}", lambda route: None)  # the answer never comes
    with tab.page.expect_request(lambda request: _is_live(request.url)):
        tab.page.click("#tab-live")

    _pill(tab, "connecting")
    assert _content(tab, "live-pill") == _text("es", "live.pill.connecting")
    assert not _shown(tab, "live-data")
    assert not _shown(tab, "live-reconnect")
    for callout in ("live-lost", "live-busy", "live-aged", "live-processes"):
        assert not _shown(tab, callout), callout


@pytest.mark.parametrize("lang", ["es", "en"])
@pytest.mark.parametrize("server_options", [FAKE])
def test_empty_shows_zeros_and_the_empty_feed(
    new_tab: NewTab, server: PanelServer, browser_name: str, lang: str
) -> None:
    tab = _tab(new_tab, lang)
    log_in(tab, server, lang)
    _open_live(tab)

    figures = _figures(tab)
    assert (figures["hidden"], figures["blocks"], figures["requests"]) == (0, 0, 0)
    assert figures["bars"] == []
    assert _shown(tab, "live-hidden-none")
    assert _shown(tab, "live-empty")
    assert _content(tab, "live-empty").strip() == _text(lang, "live.empty")
    assert [int(n) for n in re.findall(r"\d+", str(figures["apart"]))] == [0, 0]
    assert _content(tab, "live-uptime") != ""
    assert not _shown(tab, "live-processes")
    _clean(tab, browser_name)


@pytest.mark.parametrize("server_options", [ServerOptions(fake_provider=True, live_max_streams=0)])
def test_busy_says_why_and_shows_no_figures(
    new_tab: NewTab, server: PanelServer, browser_name: str
) -> None:
    tab = _tab(new_tab, "es")
    log_in(tab, server, "es")
    _open_live(tab, "off")

    assert _shown(tab, "live-busy")
    busy = CATALOGS["es"].text("live.busy.body", per_session=MAX_PER_SESSION, total=MAX_STREAMS)
    assert busy in _content(tab, "live-busy")  # both limits, the server's numbers
    assert not _shown(tab, "live-data")
    assert _shown(tab, "live-reconnect")
    assert _streams(tab) == [2]
    assert _no_live_request_within(tab, NO_RETRY_WINDOW_MS)
    _clean(tab, browser_name)


@pytest.mark.parametrize(
    "server_options", [ServerOptions(fake_provider=True, live_max_seconds=1.0)]
)
def test_max_age_closes_with_its_note_and_keeps_the_figures(
    new_tab: NewTab, server: PanelServer, browser_name: str
) -> None:
    tab = _tab(new_tab, "en")
    log_in(tab, server, "en")
    with tab.page.expect_request(lambda request: _is_live(request.url)):
        tab.page.click("#tab-live")
    tab.page.locator("#live-aged").wait_for(state="visible")

    _pill(tab, "off")
    assert _text("en", "live.aged.title") in _content(tab, "live-aged")
    assert _shown(tab, "live-data")
    assert _shown(tab, "live-reconnect")
    assert _streams(tab) == [2]
    _clean(tab, browser_name)


@pytest.mark.parametrize("server_options", [ServerOptions(fake_provider=True, processes=3)])
def test_several_processes_say_these_are_this_process_figures(
    new_tab: NewTab, server: PanelServer, browser_name: str
) -> None:
    tab = _tab(new_tab, "es")
    log_in(tab, server, "es")
    _open_live(tab)

    assert _shown(tab, "live-processes")
    body = _text("es", "live.processes.body").replace("{n}", "3")
    assert body in _content(tab, "live-processes")
    _clean(tab, browser_name)


# --- the numbers ------------------------------------------------------------------------------


@pytest.mark.parametrize("lang", ["es", "en"])
@pytest.mark.parametrize("server_options", [FAKE])
def test_the_numbers_add_up_live_and_after_a_fresh_snapshot(
    new_tab: NewTab, server: PanelServer, browser_name: str, lang: str
) -> None:
    tab = _tab(new_tab, lang)
    log_in(tab, server, lang)
    try_text(tab, "Hola, sin datos")  # a try: in the feed, never in the totals
    _open_live(tab)
    _rows(tab, 1)

    drive_feed(server)
    _rows(tab, 9)
    live = _figures(tab)
    _assert_adds_up(live)
    assert (live["hidden"], live["blocks"], live["requests"]) == (4, 2, 7)
    assert [(_number(bar["n"]), bar["v"]) for bar in live["bars"]] == [(3, "20"), (1, "7")]  # type: ignore[union-attr]
    assert _parts(str(live["requests_sub"])) == [2, 2, 2, 1]
    assert _parts(str(live["blocks_sub"])) == [1, 1]
    assert [int(n) for n in re.findall(r"\d+", str(live["apart"]))] == [1, 1]  # 1 try, 1 scan

    tab.page.click("#tab-keys")
    _open_live(tab)  # a fresh snapshot from the server
    _rows(tab, 9)
    assert _figures(tab) == live
    rows = tab.page.evaluate(ROWS_TEXT_JS)
    kinds = [row["kind"] for row in rows]
    assert kinds == [
        "scan",
        "upstream_error",
        "blocked",
        "blocked",
        "no_data",
        "no_data",
        "masked",
        "masked",
        "try",
    ]
    clock = re.compile(r"\d{1,2}[:.]\d{2}(\s?[AP]M)?")  # en-US: 3:42 PM
    assert all(clock.fullmatch(row["time"].strip()) for row in rows)
    assert _text(lang, "live.client.claude_code") in rows[-2]["who"]
    assert _text(lang, "status.provider.openai") in rows[-2]["who"]
    assert _text(lang, "live.what.not_added") in rows[0]["what"]
    assert _text(lang, "live.tag.scan") in rows[0]["tag"]
    assert _text(lang, "live.tag.try") in rows[-1]["tag"]
    assert _text(lang, "live.reason.attachment") in rows[3]["what"]
    _clean(tab, browser_name)


def _fake_stream(body: str) -> Callable[..., None]:
    def answer(route: object) -> None:
        route.fulfill(  # type: ignore[attr-defined]
            status=200,
            headers={"Content-Type": "text/event-stream", "Cache-Control": "no-store"},
            body=body,
        )

    return answer


UNKNOWN_SNAPSHOT = (
    "event: snapshot\ndata: "
    '{"recent":[{"origin":"evil_origin","client":"evil<b>client","outcome":"evil_outcome",'
    '"reason":"evil_reason","provider":"evil_provider","at":120,'
    '"types":[{"type":"EVIL_TYPE","count":2},{"type":"ES_DNI","count":"5"}]}],'
    '"hidden":[{"type":"EVIL_TYPE","count":2},{"type":"ES_DNI","count":-1}],'
    '"outcomes":[{"outcome":"masked","count":1},{"outcome":"evil_outcome","count":9}],'
    '"blocks":[{"reason":"evil_reason","count":3}],"tries":"x","scans":1,'
    '"uptime_seconds":61,"processes":1}\n\n'
)


@pytest.mark.parametrize("server_options", [FAKE])
def test_unknown_values_get_a_generic_label_and_are_never_shown(
    new_tab: NewTab, server: PanelServer
) -> None:
    tab = _tab(new_tab, "en")
    log_in(tab, server, "en")
    tab.page.route(f"{tab.origin}{LIVE_PATH}", _fake_stream(UNKNOWN_SNAPSHOT))
    with tab.page.expect_request(lambda request: _is_live(request.url)):
        tab.page.click("#tab-live")
    _rows(tab, 1)
    _pill(tab, "off")  # the made-up answer ends: closed, nothing raw left behind

    for raw in ("evil", "EVIL", "<b>"):
        assert not tab.page.evaluate(IN_VIEW_JS, raw), raw
    (row,) = tab.page.evaluate(ROWS_TEXT_JS)
    assert _text("en", "live.client.other") in row["who"]
    assert _text("en", "live.provider.unknown") in row["who"]
    assert _text("en", "live.type.unknown") in row["what"]
    assert _text("en", "status.unknown") in row["tag"]
    assert [bar["label"] for bar in tab.page.evaluate(BARS_JS)] == [
        _text("en", "live.type.unknown")
    ]
    # An unknown reason counts as «another reason»; an unknown outcome, a count that is not a
    # whole number of 1 or more and a `tries` that is not a number count nowhere.
    assert _content(tab, "live-blocks-sub") == _text("en", "live.blocks.none").replace("{n}", "3")
    assert _number(_content(tab, "live-blocks")) == 3
    assert _number(_content(tab, "live-requests")) == 1
    assert _number(_content(tab, "live-hidden")) == 2
    assert [int(n) for n in re.findall(r"\d+", _content(tab, "live-apart"))] == [0, 1]


# --- privacy ----------------------------------------------------------------------------------


@pytest.mark.parametrize("server_options", [FAKE])
def test_the_sentinel_never_reaches_the_view_the_title_or_the_console(
    new_tab: NewTab, server: PanelServer, browser_name: str
) -> None:
    tab = _tab(new_tab, "es")
    log_in(tab, server, "es")
    _open_live(tab)
    other = new_tab(locale="es-ES")  # «Prueba un texto» in another tab of the same session
    tab.share_session_with(other)
    other.goto("/panel?lang=es")
    try_text(other, f"Mi DNI es {SENTINEL_DNI}")
    wait_result(other)

    assert server.proxy_openai(f"DNI {SENTINEL_DNI}") == 200
    assert server.proxy_anthropic(f"{SENTINEL_DNI}") == 200
    assert server.scan(f"{SENTINEL_DNI}") == 200
    _rows(tab, 4)

    assert not tab.shows(SENTINEL_DNI)  # DOM, address, request URLs, console
    for needle in (SENTINEL_DNI, "[[", "ES_DNI_", "User-Agent", "curl/"):
        assert not tab.page.evaluate(IN_VIEW_JS, needle), needle
    assert tab.page.evaluate(STORAGE_JS) == {"local": 0, "session": 0, "databases": 0, "caches": 0}
    _clean(tab, browser_name)


# --- accessibility and layout -------------------------------------------------------------------


def reach_state(
    tab: Tab, server: PanelServer, state: str, lang: str, *, with_data: bool = False
) -> None:
    """Log in and bring «En directo» to `state` (a key of STATES); `with_data`: the scenario
    of the numbers first (the data state always has it)."""
    log_in(tab, server, lang)
    if state == "connecting":
        tab.page.route(f"{tab.origin}{LIVE_PATH}", lambda route: None)
        with tab.page.expect_request(lambda request: _is_live(request.url)):
            tab.page.click("#tab-live")
        _pill(tab, "connecting")
        return
    if state == "data" or with_data:
        drive_feed(server)
    if state == "busy":
        _open_live(tab, "off")
        return
    if state == "aged":
        with tab.page.expect_request(lambda request: _is_live(request.url)):
            tab.page.click("#tab-live")
        tab.page.locator("#live-aged").wait_for(state="visible")
        return
    _open_live(tab)
    if state == "lost":
        server.cut_live_streams()
        assert server.scan("hola") == 200
        _pill(tab, "off")


STATES = {
    "data": FAKE,
    "empty": FAKE,
    "connecting": FAKE,
    "lost": FAKE,
    "busy": ServerOptions(fake_provider=True, live_max_streams=0),
    "aged": ServerOptions(fake_provider=True, live_max_seconds=1.0),
    "processes": ServerOptions(fake_provider=True, processes=3),
}


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("lang", ["es", "en"])
@pytest.mark.parametrize(("state", "server_options"), list(STATES.items()), ids=list(STATES))
def test_axe_finds_nothing_in_every_state(
    new_tab: NewTab, server: PanelServer, state: str, lang: str, theme: str
) -> None:
    tab = _tab(new_tab, lang, color_scheme=theme)
    reach_state(tab, server, state, lang)
    tab.page.evaluate(SETTLE_JS)

    report = tab.axe()
    assert report.violations == "", report.violations
    assert report.passes > 0


@pytest.mark.parametrize("width", [320, 390])
@pytest.mark.parametrize("server_options", [FAKE])
def test_the_phone_has_no_horizontal_scroll_and_two_line_rows(
    new_tab: NewTab, server: PanelServer, width: int
) -> None:
    tab = _tab(new_tab, "es", width=width)
    reach_state(tab, server, "data", "es")
    tab.page.evaluate(SETTLE_JS)

    assert tab.page.evaluate(PAGE_OVERFLOW_JS) <= 0
    assert tab.page.evaluate(CLIPPED_JS) == []
    box = tab.page.evaluate("""() => { const li = document.querySelector('#live-feed > li');
      const r = (s) => li.querySelector(s).getBoundingClientRect();
      return {time: r('time'), tag: r('.tag'), main: r('.ev-main')}; }""")
    assert box["tag"]["top"] >= box["time"]["bottom"] - 1  # the tag under the time
    assert abs(box["tag"]["right"] - box["time"]["right"]) <= 1  # both on the right
    assert box["main"]["right"] <= box["tag"]["left"] + 1


@pytest.mark.parametrize("width", [390, 1280])
@pytest.mark.parametrize("server_options", [FAKE])
def test_text_at_200_percent_clips_nothing(
    new_tab: NewTab, server: PanelServer, width: int
) -> None:
    tab = _tab(new_tab, "es", width=width, bypass_csp=True)
    reach_state(tab, server, "data", "es")
    tab.page.add_style_tag(content="html{font-size:200%}")
    tab.page.evaluate(SETTLE_JS)

    assert tab.page.evaluate(CLIPPED_JS) == []
    assert tab.page.evaluate(PAGE_OVERFLOW_JS) <= 0


FORCED_JS = """() => {
  const fill = getComputedStyle(document.querySelector('#live-bars .meter > i')).backgroundColor;
  const track = getComputedStyle(document.querySelector('#live-bars .meter')).backgroundColor;
  const text = getComputedStyle(document.querySelector('#live-bars .n')).color;
  const dot = getComputedStyle(document.querySelector('#live-pill .dot')).backgroundColor;
  return {fill, track, text, dot};
}"""


@pytest.mark.parametrize("server_options", [FAKE])
def test_forced_colours_keep_the_bars_and_the_dot(
    new_tab: NewTab, server: PanelServer, browser_name: str
) -> None:
    tab = _tab(new_tab, "es", forced_colors="active")
    reach_state(tab, server, "data", "es")
    tab.page.evaluate(SETTLE_JS)

    colours = tab.page.evaluate(FORCED_JS)
    if browser_name in ("chromium", "firefox"):  # engines that really force the colours
        assert colours["fill"] == colours["text"]
        assert colours["fill"] != colours["track"]
        assert colours["dot"] == colours["text"]
    report = tab.axe()
    assert report.violations == "", report.violations


def _snapshot(**fields: object) -> str:
    """A made-up `snapshot` frame (the closed schema, empty but for `fields`)."""
    import json

    data: dict[str, object] = {
        "recent": [],
        "hidden": [],
        "outcomes": [],
        "blocks": [],
        "tries": 0,
        "scans": 0,
        "uptime_seconds": 0,
        "processes": 1,
        **fields,
    }
    return "event: snapshot\ndata: " + json.dumps(data) + "\n\n"


def _show_made_up(tab: Tab, body: str) -> None:
    tab.page.route(f"{tab.origin}{LIVE_PATH}", _fake_stream(body))
    with tab.page.expect_request(lambda request: _is_live(request.url)):
        tab.page.click("#tab-live")
    _pill(tab, "off")  # the made-up answer ends: the stream is closed


@pytest.mark.parametrize("server_options", [FAKE])
def test_a_small_count_still_shows_a_bar(new_tab: NewTab, server: PanelServer) -> None:
    """Review 1 (L3): 1 next to 100 rounds to 0 twentieths; a count above zero keeps one."""
    tab = _tab(new_tab, "en")
    log_in(tab, server, "en")
    hidden = [{"type": "ES_DNI", "count": 100}, {"type": "EMAIL", "count": 1}]
    _show_made_up(tab, _snapshot(hidden=hidden))

    assert [bar["v"] for bar in tab.page.evaluate(BARS_JS)] == ["20", "1"]


@pytest.mark.parametrize("lang", ["es", "en"])
@pytest.mark.parametrize("server_options", [FAKE])
def test_the_uptime_numbers_follow_the_language(
    new_tab: NewTab, server: PanelServer, lang: str
) -> None:
    """Review 1 (L6): 12345 days read «12.345» in Spanish and «12,345» in English (Spanish
    groups only from five digits)."""
    tab = _tab(new_tab, lang)
    log_in(tab, server, lang)
    _show_made_up(tab, _snapshot(uptime_seconds=12345 * 86400 + 3600))

    assert ("12.345" if lang == "es" else "12,345") in _content(tab, "live-uptime")


@pytest.mark.parametrize("server_options", [FAKE])
def test_a_time_out_of_range_leaves_the_time_empty(
    new_tab: NewTab, server: PanelServer, browser_name: str
) -> None:
    """Privacy review INFO 3: a time no date can hold never breaks the view: an empty <time>."""
    tab = _tab(new_tab, "en")
    log_in(tab, server, "en")
    event = {
        "origin": "scan",
        "client": "curl",
        "outcome": "no_data",
        "reason": "none",
        "provider": "none",
        "types": [],
    }
    recent = [{**event, "at": 10**20}, {**event, "at": 120}]
    _show_made_up(tab, _snapshot(recent=recent))
    _rows(tab, 2)

    times = [row["time"] for row in tab.page.evaluate(ROWS_TEXT_JS)]
    assert times[0] == "" and times[1] != ""
    assert tab.errors == []


@pytest.mark.parametrize("lang", ["es", "en"])
@pytest.mark.parametrize("server_options", [FAKE])
def test_lost_before_any_figure_does_not_speak_of_figures(
    new_tab: NewTab, server: PanelServer, lang: str
) -> None:
    """Review 1 (M4): with no snapshot ever, the lost callout has no «cifras de antes»."""
    tab = _tab(new_tab, lang)
    log_in(tab, server, lang)
    tab.page.route(f"{tab.origin}{LIVE_PATH}", lambda route: route.abort())
    with tab.page.expect_request(lambda request: _is_live(request.url)):
        tab.page.click("#tab-live")
    _pill(tab, "off")

    assert _shown(tab, "live-lost")
    assert _shown(tab, "live-lost-none")
    assert not _shown(tab, "live-lost-figures")
    assert not _shown(tab, "live-data")


@pytest.mark.parametrize("server_options", [FAKE])
def test_lost_after_a_snapshot_speaks_of_the_figures_from_before(
    new_tab: NewTab, server: PanelServer
) -> None:
    tab = _tab(new_tab, "es")
    log_in(tab, server, "es")
    _open_live(tab)
    server.cut_live_streams()
    assert server.scan("hola") == 200
    _pill(tab, "off")

    assert _shown(tab, "live-lost-figures")
    assert not _shown(tab, "live-lost-none")


@pytest.mark.parametrize("server_options", [FAKE])
def test_pagehide_closes_the_stream(new_tab: NewTab, server: PanelServer) -> None:
    """Review 1 (L2). Already green when written: live.js closed on pagehide before."""
    tab = _tab(new_tab, "en")
    log_in(tab, server, "en")
    _open_live(tab)

    tab.page.evaluate("() => window.dispatchEvent(new Event('pagehide'))")
    assert _streams(tab) == [2]
    _eventually(lambda: server.live_streams_open() == 0, "the place is free")
