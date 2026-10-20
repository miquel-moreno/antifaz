"""Red team of «Prueba un texto» in real browsers (issue 53, PR 11d-b): Chromium, Firefox, WebKit.

The sentinel DNI and an invented name go into the field. They may travel ONLY in the body of the
one POST /panel/api/try and come back only into that view:

- never in a URL, the address bar or the history; no other request than that POST;
- nothing in localStorage, sessionStorage, IndexedDB, the Cache API or a new cookie;
- never in another view, the title, the console or the gateway's logs (everything at DEBUG);
- never after a reload, nor after «Salir» and the back button;
- the live region holds counts only;
- markup typed into the field is shown as text (no element is made of it, no script runs);
- without JavaScript «Probar» is disabled: nothing at all is sent;
- another site, or another localhost port, can neither try a text nor extend the session.

No token, cookie value or page HTML inside an assert (tests/unit/test_browser_harness_rules.py).
"""

import json
import logging
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Request
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from antifaz.web.i18n import load_catalogs
from tests.browser.harness import NewTab, Tab
from tests.browser.server import OtherSite, PanelServer, ServerOptions
from tests.browser.test_panel_try import (
    TRY_PATH,
    clean,
    hidden,
    log_in,
    text_of,
    try_text,
    wait_result,
)
from tests.browser.test_panel_ui import (
    XSITE_FORM,
    XSITE_JSON_FETCH,
    XSITE_PLAIN_FETCH,
    _assert_every_post_refused,
    _go_to_other_site,
    _log_in_with_the_form,
    _open_other_site,
)
from tests.conftest import SENTINEL_DNI
from tests.nerfakes import CARMEN

pytestmark = pytest.mark.browser

CATALOGS = load_catalogs()
SENTINEL_TEXT = f"Mi DNI es {SENTINEL_DNI} y firmo {CARMEN}."
STORAGE_JS = """async () => ({
  local: localStorage.length,
  session: sessionStorage.length,
  databases: indexedDB.databases ? (await indexedDB.databases()).length : 0,
  caches: self.caches ? (await caches.keys()).length : 0,
})"""
HISTORY_JS = "() => history.length"
VIEW_TEXT_JS = "(id) => document.getElementById(id).innerText"


@pytest.fixture
def server_options() -> ServerOptions:
    return ServerOptions(ner_names=True)


def _shows_nothing_of_it(text: str) -> bool:
    return SENTINEL_DNI not in text and CARMEN not in text


def test_the_sentinel_travels_only_in_the_try_body(
    tab: Tab, server: PanelServer, caplog: pytest.LogCaptureFixture, browser_name: str
) -> None:
    caplog.set_level(logging.DEBUG)
    log_in(tab, server)
    start, history = tab.page.url, tab.page.evaluate(HISTORY_JS)
    seen: list[Request] = []
    tab.page.on("request", lambda request: seen.append(request))

    try_text(tab, SENTINEL_TEXT)
    wait_result(tab)

    assert [(r.method, urlsplit(r.url).path) for r in seen] == [("POST", TRY_PATH)]
    body = seen[0].post_data or ""
    assert SENTINEL_DNI in body  # it really went, in the body only
    assert all(_shows_nothing_of_it(url) for url in tab.requests)
    assert tab.page.url == start and tab.page.evaluate(HISTORY_JS) == history
    assert tab.page.evaluate(STORAGE_JS) == {"local": 0, "session": 0, "databases": 0, "caches": 0}
    assert tab.cookie_names() == ["antifaz_session"]
    assert _shows_nothing_of_it(tab.page.title())
    assert all(_shows_nothing_of_it(line) for line in tab.console_texts)
    live = text_of(tab, "try-live")
    assert _shows_nothing_of_it(live) and live == CATALOGS["es"].text("try.meta.hidden", n=2)
    for view in ("status", "live", "keys", "policies"):
        tab.page.click(f"#tab-{view}")
        assert _shows_nothing_of_it(tab.page.evaluate(VIEW_TEXT_JS, f"panel-{view}")), view
    assert _shows_nothing_of_it(caplog.text)
    assert caplog.records  # the gateway logged meanwhile
    clean(tab, browser_name)


def test_nothing_is_left_after_a_reload_or_after_leaving_and_going_back(
    tab: Tab, server: PanelServer
) -> None:
    log_in(tab, server)
    try_text(tab, SENTINEL_TEXT)
    wait_result(tab)

    tab.page.reload(wait_until="load")
    page = tab.page.content()
    assert _shows_nothing_of_it(page)
    assert tab.page.input_value("#try-text") == "" and hidden(tab, "try-result")

    try_text(tab, SENTINEL_TEXT)
    wait_result(tab)
    tab.page.click(".app-sidebar-foot [data-logout]")
    tab.page.wait_for_url(f"{server.origin}/panel/login?lang=es")
    tab.page.go_back(wait_until="load")
    page = tab.page.content()
    assert _shows_nothing_of_it(page)


def test_markup_in_the_field_is_shown_as_text(
    tab: Tab, server: PanelServer, browser_name: str
) -> None:
    payload = (
        "<img src=x onerror=\"document.title='pwned'\"> ana.prueba@example.com "
        "<script>document.title='pwned'</script>"
    )
    log_in(tab, server)
    title = tab.page.title()

    try_text(tab, payload)
    wait_result(tab)

    assert tab.page.locator("#try-panes img, #try-panes script").count() == 0
    assert text_of(tab, "try-input") == payload
    assert "<img src=x onerror=" in text_of(tab, "try-sent")
    assert "<script>" in text_of(tab, "try-reply") or "<script>" in text_of(tab, "try-sent")
    assert tab.page.title() == title
    clean(tab, browser_name)


def test_without_javascript_nothing_is_sent_at_all(new_tab: NewTab, server: PanelServer) -> None:
    """Privacy review of 11d-b: «Probar» is disabled in the HTML (try.js turns it on), so without
    a script pressing it sends nothing, not even an empty form."""
    scripted = new_tab()
    log_in(scripted, server)
    tab = new_tab(javascript=False)
    scripted.share_session_with(tab)
    tab.goto("/panel?lang=es")
    assert tab.page.locator("noscript").count() == 1  # its text: tests/unit/test_web_try_view.py
    assert tab.page.locator("#try-submit").is_disabled()
    before = list(tab.requests)

    tab.page.fill("#try-text", SENTINEL_TEXT)
    with (
        pytest.raises(PlaywrightTimeoutError),
        tab.page.expect_request(lambda request: request.method == "POST", timeout=2000),
    ):
        tab.page.click("#try-submit", force=True)
        tab.page.press("#try-text", "Control+Enter")

    assert tab.requests == before
    assert server.door_statuses("/panel") == []


def _attack_api(tab: Tab, server: PanelServer, path: str, body: str, form_name: str) -> None:
    """test_panel_ui's three tries of another site. Under Playwright's routing, Chromium may let
    the page read the JSON fetch's answer (Playwright answers the CORS preflight itself): then it
    must be the door's 403, the same refusal the other two get."""
    url = server.origin + path
    before = len(server.door_statuses(path))
    json_result = tab.page.evaluate(XSITE_JSON_FETCH, [url, body])
    assert json_result in ("blocked", "answered 403")
    plain_result = tab.page.evaluate(XSITE_PLAIN_FETCH, [url, body])
    assert plain_result in ("opaque", "blocked")
    assert server.door_headers(path).get("origin") != server.origin
    with tab.page.expect_navigation(wait_until="commit"):
        tab.page.evaluate(XSITE_FORM, [url, form_name, '"}'])
    assert len(server.door_statuses(path)) >= before + 2


def test_another_site_cannot_try_a_text_nor_extend_the_session(
    new_tab: NewTab, server: PanelServer, other_site: OtherSite
) -> None:
    tab = _open_other_site(new_tab, other_site)
    tab.goto("/panel/login")
    _log_in_with_the_form(tab, server)
    _go_to_other_site(tab, other_site)

    _attack_api(tab, server, "/panel/api/session", "{}", '{"x": "')
    _go_to_other_site(tab, other_site)
    body = json.dumps({"text": SENTINEL_TEXT, "lang": "es"})
    _attack_api(tab, server, TRY_PATH, body, '{"text": "x", "lang": "es", "y": "')

    _assert_every_post_refused(server, "/panel/api/session")
    _assert_every_post_refused(server, TRY_PATH)
    assert tab.blocked == []
