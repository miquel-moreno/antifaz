"""The «Estado» view in real browsers (issue 53, PR 11c-3b, ADR-0018): Chromium, Firefox, WebKit.

The gateway of these tests has throwaway provider keys and a fake NER that says "ok" (its
transport still refuses every provider call, and the fixture checks nothing tried).

- When it asks: never at page load; exactly once when the view is first shown; again on
  «Actualizar» (the live region says when); with the page's clock moved 30 minutes on, never;
  when the page comes back to visible only if «Estado» is the selected view; never while the
  page is hidden or another view is selected.
- Failures: a network error shows the catalog's callout in the page's language, keeps the last
  data marked as old and leaves the focus on the button; a 401 goes to the login with ?lang.
- An answer with unknown enums shows the "unknown" label, never the raw value.
- No CSP violation, a clean console and no foreign request; axe finds nothing (loaded and error
  state, light and dark, es and en, desktop and phone); at 200 % text every text of the loaded
  view grows at least 1.8 times and nothing clips or spills.
"""

import contextlib
import json
import re
import sys

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Response, Route

from antifaz.web.i18n import load_catalogs
from tests.browser.console_rules import unexpected_console
from tests.browser.harness import TIMEOUT_MS, NewTab, Tab
from tests.browser.server import PanelServer, ServerOptions
from tests.browser.test_panel_text_size import SETTLE_JS, SIZES_JS, TEXT_200_CSS

pytestmark = pytest.mark.browser

CATALOGS = load_catalogs()
STATUS_PATH = "/panel/api/status"
CHECKED_JS = """(texts) => {
  const line = document.getElementById('status-time').textContent;
  return texts.some((text) => line.startsWith(text));
}"""
IDLE_JS = "() => document.getElementById('status-refresh').getAttribute('aria-disabled') !== 'true'"
ACTIVE_ID_JS = "() => document.activeElement ? document.activeElement.id : null"
# Make the page look hidden or visible again, and tell it (headless pages are always visible).
VISIBILITY_JS = """(state) => {
  Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => state});
  Object.defineProperty(document, 'hidden', {configurable: true, get: () => state === 'hidden'});
  document.dispatchEvent(new Event('visibilitychange'));
}"""
VIEW_TEXT_JS = "() => document.getElementById('panel-status').innerText"
# Every element with text of its own in the loaded view: does it clip (overflow hidden and wider
# content) or go past the window's right edge?
CLIPPED_JS = """() => [...document.querySelectorAll('#panel-status *')].filter((el) =>
  [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim() !== '') &&
  el.getClientRects().length > 0 && (
    (getComputedStyle(el).overflowX !== 'visible' && el.scrollWidth > el.clientWidth + 1) ||
    el.getBoundingClientRect().right > window.innerWidth + 1)).map((el) =>
  el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + ' ' +
  el.textContent.trim().slice(0, 30))"""
PAGE_OVERFLOW_JS = "() => document.documentElement.scrollWidth - window.innerWidth"
# The 11c-3a walk, over the loaded «Estado» view only (the 11c-3a test covers the whole page as it
# loads). Firefox and WebKit keep the old computed style of a view that was shown and is hidden
# again, so the other views are not measured here.
STATUS_SIZES_JS = SIZES_JS.replace(
    "document.body.querySelectorAll('*')", "document.querySelectorAll('#panel-status *')"
)


@pytest.fixture
def server_options() -> ServerOptions:
    return ServerOptions(provider_keys=True, ner_state="ok")


def _locale(lang: str) -> str:
    return "es-ES" if lang == "es" else "en-US"


def _texts(lang: str, key: str) -> str:
    return CATALOGS[lang].text(key)


def _checked_prefix(lang: str, key: str = "status.checked") -> str:
    return _texts(lang, key).split("{time}", 1)[0]


def _log_in(tab: Tab, server: PanelServer, lang: str) -> None:
    tab.goto(f"/panel/login?lang={lang}")
    tab.page.fill("#login-token", server.admin_token)
    tab.page.click("#login-form button[type=submit]")
    tab.page.wait_for_url(f"{server.origin}/panel?lang={lang}")
    tab.page.evaluate(SETTLE_JS)
    tab.remember_secrets()


def _status_requests(tab: Tab) -> int:
    return len([url for url in tab.requests if url.split("?", 1)[0].endswith(STATUS_PATH)])


def _is_status(response: Response) -> bool:
    return response.url.split("?", 1)[0].endswith(STATUS_PATH)


def _wait_checked(tab: Tab, lang: str, key: str = "status.checked") -> None:
    tab.page.wait_for_function(CHECKED_JS, arg=[_checked_prefix(lang, key)], timeout=TIMEOUT_MS)
    tab.page.wait_for_function(IDLE_JS, timeout=TIMEOUT_MS)


def _open_status(tab: Tab, lang: str) -> None:
    with tab.page.expect_response(_is_status):
        tab.page.click("#tab-status")
    _wait_checked(tab, lang)


def _clean(tab: Tab, browser_name: str) -> None:
    assert tab.csp_violations() == []
    assert unexpected_console(tab.console, browser_name, sys.platform) == []
    assert tab.errors == []
    assert tab.failed == []
    assert tab.blocked == []
    assert tab.foreign_requests() == []


# --- when it asks -------------------------------------------------------------------------------


def test_nothing_at_load_and_one_request_when_first_shown(
    tab: Tab, server: PanelServer, browser_name: str
) -> None:
    _log_in(tab, server, "en")
    assert _status_requests(tab) == 0

    _open_status(tab, "en")
    assert _status_requests(tab) == 1

    tab.page.click("#tab-keys")
    tab.page.click("#tab-status")
    tab.page.wait_for_function(IDLE_JS, timeout=TIMEOUT_MS)
    assert _status_requests(tab) == 1  # shown again: no new request
    assert tab.page.locator("#status-version").inner_text() != ""
    assert tab.page.locator('[data-ner="ok"]').get_attribute("aria-current") == "true"
    assert tab.page.locator("#status-grid").get_attribute("aria-busy") == "false"
    _clean(tab, browser_name)


@pytest.mark.parametrize("lang", ["es", "en"])
def test_refresh_asks_again_and_the_live_region_says_when(
    new_tab: NewTab, server: PanelServer, browser_name: str, lang: str
) -> None:
    tab = new_tab(locale=_locale(lang))
    _log_in(tab, server, lang)
    _open_status(tab, lang)

    with tab.page.expect_response(_is_status):
        tab.page.click("#status-refresh")
    _wait_checked(tab, lang)

    assert _status_requests(tab) == 2
    line = tab.page.locator("#status-time")
    assert line.get_attribute("role") == "status"
    assert re.search(r"\d", line.inner_text())  # the time, from Intl in the page's language
    assert tab.page.evaluate(ACTIVE_ID_JS) == "status-refresh"
    assert tab.page.locator("#status-alert").is_hidden()
    _clean(tab, browser_name)


def test_thirty_minutes_later_nothing_was_asked(
    tab: Tab, server: PanelServer, browser_name: str
) -> None:
    tab.page.clock.install()
    _log_in(tab, server, "en")
    _open_status(tab, "en")
    before = _status_requests(tab)

    tab.page.clock.run_for(30 * 60 * 1000)
    tab.page.evaluate(SETTLE_JS)

    assert _status_requests(tab) == before == 1
    _clean(tab, browser_name)


def test_hidden_or_another_view_never_asks_and_visible_on_status_asks_once(
    tab: Tab, server: PanelServer, browser_name: str
) -> None:
    _log_in(tab, server, "en")
    tab.page.evaluate(VISIBILITY_JS, "hidden")
    tab.page.evaluate(VISIBILITY_JS, "visible")
    assert _status_requests(tab) == 0  # Status was never shown

    _open_status(tab, "en")
    tab.page.evaluate(VISIBILITY_JS, "hidden")
    assert _status_requests(tab) == 1  # nothing while hidden

    with tab.page.expect_response(_is_status):
        tab.page.evaluate(VISIBILITY_JS, "visible")
    _wait_checked(tab, "en")
    assert _status_requests(tab) == 2  # back to visible on «Estado»: once

    tab.page.click("#tab-live")
    tab.page.evaluate(VISIBILITY_JS, "hidden")
    tab.page.evaluate(VISIBILITY_JS, "visible")
    tab.page.evaluate(SETTLE_JS)
    assert _status_requests(tab) == 2  # another view selected: nothing
    _clean(tab, browser_name)


# --- failures -------------------------------------------------------------------------------------


def _abort_status(tab: Tab) -> None:
    tab.page.route(f"{tab.origin}{STATUS_PATH}", lambda route: route.abort())


@pytest.mark.parametrize("lang", ["es", "en"])
def test_a_network_error_shows_the_callout_and_keeps_the_old_data(
    new_tab: NewTab, server: PanelServer, lang: str
) -> None:
    tab = new_tab(locale=_locale(lang))
    _log_in(tab, server, lang)
    _open_status(tab, lang)
    version = tab.page.locator("#status-version").inner_text()
    _abort_status(tab)

    tab.page.click("#status-refresh")
    tab.page.locator("#status-alert").wait_for(state="visible")
    _wait_checked(tab, lang, "status.checked.old")

    alert = tab.page.locator("#status-alert")
    assert alert.get_attribute("role") == "alert"
    assert _texts(lang, "status.error.network") in alert.inner_text()
    assert tab.page.locator("#status-version").inner_text() == version  # the last data kept
    assert tab.page.locator("#status-grid").is_visible()
    assert tab.page.evaluate(ACTIVE_ID_JS) == "status-refresh"
    assert tab.csp_violations() == []
    assert tab.errors == []
    assert tab.foreign_requests() == []


def test_a_401_goes_to_the_login_with_the_language(tab: Tab, server: PanelServer) -> None:
    _log_in(tab, server, "es")
    tab.page.route(
        f"{tab.origin}{STATUS_PATH}",
        lambda route: route.fulfill(status=401, json={"error": {"code": "panel_unauthorized"}}),
    )

    tab.page.click("#tab-status")

    tab.page.wait_for_url(f"{server.origin}/panel/login?lang=es")
    assert tab.page.locator("html").get_attribute("lang") == "es"


UNKNOWN_ANSWER = {
    "schema": 7,
    "version": "9.9.9 RAWVERSION",
    "uptime_seconds": "RAWUPTIME",
    "panel_scheme": "RAWSCHEME",
    "detectors": {"validators": "on", "patterns": "on", "ner": "RAWNER"},
    "providers": [
        {"id": "openai", "has_key": "RAWKEY", "address": "RAW/ADDRESS?x", "has_credentials": 1},
        {"id": "RAWPROVIDER", "has_key": True, "address": "default", "has_credentials": False},
    ],
    "limits": {"allowed_hosts": "RAWHOSTS", "allowed_origins": -1, "trusted_proxies": 1.5},
    "overall": "RAWOVERALL",
}


def _fulfill_unknown(route: Route) -> None:
    route.fulfill(status=200, content_type="application/json", body=json.dumps(UNKNOWN_ANSWER))


@pytest.mark.parametrize("lang", ["es", "en"])
def test_unknown_values_show_the_unknown_label_never_the_raw_string(
    new_tab: NewTab, server: PanelServer, lang: str
) -> None:
    tab = new_tab(locale=_locale(lang))
    _log_in(tab, server, lang)
    tab.page.route(f"{tab.origin}{STATUS_PATH}", _fulfill_unknown)

    _open_status(tab, lang)

    text = tab.page.evaluate(VIEW_TEXT_JS)
    assert "RAW" not in text
    assert "9.9.9" not in text
    unknown = _texts(lang, "status.unknown")
    for selector in ("#status-overall", "#status-ner", "#status-scheme", "#status-uptime"):
        assert unknown in tab.page.locator(selector).inner_text(), selector
    assert unknown in tab.page.locator('[data-provider="openai"] [data-address]').inner_text()
    assert tab.page.locator("[data-ner][aria-current]").count() == 0


# --- review of 11c-3b: announcements, focus, in-flight requests ---------------------------------

# Press the button and read, in the same task (before any answer can arrive), what the page shows.
PRESS_AND_READ_JS = """() => {
  const button = document.getElementById('status-refresh');
  button.click();
  return {alertHidden: document.getElementById('status-alert').hidden,
          alertText: document.getElementById('status-alert-text').textContent,
          busy: document.getElementById('status-grid').getAttribute('aria-busy'),
          ariaDisabled: button.getAttribute('aria-disabled'), disabled: button.disabled,
          focused: document.activeElement === button};
}"""
FOCUS_BUTTON_JS = "() => document.getElementById('status-refresh').focus()"
DOUBLE_PRESS_JS = """() => { const b = document.getElementById('status-refresh');
  b.click(); b.click(); b.click(); }"""
FLAP_DURING_PRESS_JS = """() => {
  const set = (state) => {
    Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => state});
    document.dispatchEvent(new Event('visibilitychange'));
  };
  document.getElementById('status-refresh').click();
  set('hidden'); set('visible'); set('hidden'); set('visible');
}"""
PILL_JS = """(id) => { const p = document.getElementById(id);
  return {label: p.querySelector('[data-label]').textContent,
          tones: ['ok', 'warn', 'danger', 'neutral'].filter((t) => p.classList.contains(t))}; }"""
STALE_JS = "() => document.getElementById('status-grid').classList.contains('is-stale')"


@pytest.mark.parametrize("lang", ["es", "en"])
def test_a_failure_after_a_failure_is_announced_again(
    new_tab: NewTab, server: PanelServer, lang: str
) -> None:
    tab = new_tab(locale=_locale(lang))
    _log_in(tab, server, lang)
    _abort_status(tab)
    tab.page.click("#tab-status")
    tab.page.locator("#status-alert").wait_for(state="visible")

    pressed = tab.page.evaluate(PRESS_AND_READ_JS)
    tab.page.locator("#status-alert").wait_for(state="visible")

    assert pressed["alertHidden"] is True  # hidden while checking...
    assert pressed["alertText"] == ""
    assert pressed["busy"] == "true"
    text = tab.page.locator("#status-alert").inner_text()
    assert _texts(lang, "status.error.network") in text  # ...and shown again, fresh


def test_the_focus_stays_on_the_button_through_a_refresh(tab: Tab, server: PanelServer) -> None:
    _log_in(tab, server, "en")
    _open_status(tab, "en")
    tab.page.evaluate(FOCUS_BUTTON_JS)

    with tab.page.expect_response(_is_status):
        pressed = tab.page.evaluate(PRESS_AND_READ_JS)
    _wait_checked(tab, "en")

    assert pressed["ariaDisabled"] == "true"
    assert pressed["disabled"] is False  # never disabled: the focus would be lost
    assert pressed["focused"] is True
    assert tab.page.evaluate(ACTIVE_ID_JS) == "status-refresh"
    assert tab.page.locator("#status-refresh").get_attribute("aria-disabled") in (None, "false")


def test_presses_while_one_is_in_flight_ask_once(tab: Tab, server: PanelServer) -> None:
    _log_in(tab, server, "en")
    _open_status(tab, "en")

    with tab.page.expect_response(_is_status):
        tab.page.evaluate(DOUBLE_PRESS_JS)
    _wait_checked(tab, "en")

    assert _status_requests(tab) == 2


def test_visibility_flapping_during_a_request_asks_at_most_once_more(
    tab: Tab, server: PanelServer
) -> None:
    _log_in(tab, server, "en")
    _open_status(tab, "en")

    with tab.page.expect_response(_is_status):
        tab.page.evaluate(FLAP_DURING_PRESS_JS)
    _wait_checked(tab, "en")
    tab.page.evaluate(SETTLE_JS)

    assert _status_requests(tab) == 2  # the press; every visible event came while in flight


# A bound on waiting for a route event, counted in page round trips (never a time limit).
ROUND_TRIPS = 200


def _answer(ner: str, overall: str) -> str:
    return json.dumps(
        {
            "schema": 1,
            "version": "0.1.1",
            "uptime_seconds": 7200,
            "panel_scheme": "http_localhost",
            "detectors": {"validators": "on", "patterns": "on", "ner": ner},
            "providers": [
                {"id": "openai", "has_key": True, "address": "default", "has_credentials": False},
                {
                    "id": "anthropic",
                    "has_key": True,
                    "address": "default",
                    "has_credentials": False,
                },
            ],
            "limits": {"allowed_hosts": 2, "allowed_origins": 0, "trusted_proxies": 0},
            "overall": overall,
        }
    )


def test_only_the_latest_answer_renders(tab: Tab, server: PanelServer) -> None:
    """The first request is held; «Actualizar» replaces it (the first is aborted) and its answer
    renders; the held answer, released afterwards, changes nothing."""
    held: list[Route] = []

    def handler(route: Route) -> None:
        if not held:
            held.append(route)  # answered later, or never
            return
        route.fulfill(status=200, content_type="application/json", body=_answer("ok", "ok"))

    _log_in(tab, server, "en")
    tab.page.route(f"{tab.origin}{STATUS_PATH}", handler)
    tab.page.click("#tab-status")
    for _ in range(ROUND_TRIPS):  # let the route event arrive: a few round trips at most
        if held:
            break
        tab.page.evaluate(SETTLE_JS)
    assert held, "the first request never reached the route"
    with tab.page.expect_response(_is_status):
        # A person can press it while it reads (aria-disabled, never disabled): Playwright's
        # click() waits for "enabled", so the press is dispatched directly.
        tab.page.locator("#status-refresh").dispatch_event("click")
    _wait_checked(tab, "en")
    with contextlib.suppress(PlaywrightError):  # the page already aborted it: that is the point
        held[0].fulfill(
            status=200, content_type="application/json", body=_answer("closed", "danger")
        )
    tab.page.evaluate(SETTLE_JS)

    assert tab.page.evaluate(PILL_JS, "status-ner")["tones"] == ["ok"]
    assert tab.page.evaluate(PILL_JS, "status-overall")["tones"] == ["ok"]
    assert tab.page.locator("#status-alert").is_hidden()


@pytest.mark.parametrize("lang", ["es", "en"])
def test_after_an_error_the_overall_pill_is_not_refreshed_and_the_grid_is_dimmed(
    new_tab: NewTab, server: PanelServer, lang: str
) -> None:
    tab = new_tab(locale=_locale(lang))
    _log_in(tab, server, lang)
    _open_status(tab, lang)
    assert tab.page.evaluate(PILL_JS, "status-overall")["tones"] == ["ok"]
    _abort_status(tab)

    tab.page.click("#status-refresh")
    _wait_checked(tab, lang, "status.checked.old")

    pill = tab.page.evaluate(PILL_JS, "status-overall")
    assert pill == {"label": _texts(lang, "status.overall.stale"), "tones": ["neutral"]}
    assert tab.page.evaluate(STALE_JS) is True
    with tab.page.expect_response(_is_status):
        tab.page.unroute(f"{tab.origin}{STATUS_PATH}")
        tab.page.click("#status-refresh")
    _wait_checked(tab, lang)
    assert tab.page.evaluate(STALE_JS) is False


WARNINGS_ANSWER = {
    "schema": 1,
    "version": "0.1.1",
    "uptime_seconds": 3 * 86400 + 4 * 3600,
    "panel_scheme": "https",
    "detectors": {"validators": "on", "patterns": "on", "ner": "closed"},
    "providers": [
        {
            "id": "openai",
            "has_key": True,
            "address": "proxy.example:8443",
            "has_credentials": True,
        },
        {"id": "anthropic", "has_key": False, "address": "default", "has_credentials": False},
    ],
    "limits": {"allowed_hosts": 3, "allowed_origins": 1, "trusted_proxies": 1},
    "overall": "danger",
}


def fulfill_warnings(route: Route) -> None:
    """A valid answer with every warning at once (also used by the captures)."""
    route.fulfill(status=200, content_type="application/json", body=json.dumps(WARNINGS_ANSWER))


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("lang", ["es", "en"])
def test_every_warning_renders_and_axe_finds_nothing(
    new_tab: NewTab, server: PanelServer, lang: str, theme: str
) -> None:
    tab = new_tab(bypass_csp=True, color_scheme=theme, locale=_locale(lang))
    _log_in(tab, server, lang)
    tab.page.route(f"{tab.origin}{STATUS_PATH}", fulfill_warnings)

    _open_status(tab, lang)

    assert tab.page.evaluate(PILL_JS, "status-overall") == {
        "label": _texts(lang, "status.overall.danger"),
        "tones": ["danger"],
    }
    assert tab.page.evaluate(PILL_JS, "status-ner")["tones"] == ["danger"]
    assert tab.page.locator('[data-ner="closed"]').get_attribute("aria-current") == "true"
    openai = tab.page.locator('[data-provider="openai"]')
    assert openai.locator("[data-address]").inner_text() == "proxy.example:8443"
    assert openai.locator("[data-credentials]").is_visible()
    anthropic = tab.page.locator('[data-provider="anthropic"]')
    assert _texts(lang, "status.key.no") in anthropic.inner_text()
    assert anthropic.locator("[data-credentials]").is_hidden()
    assert tab.page.locator("#status-uptime").inner_text() == "3 d 4 h"
    report = tab.axe()
    assert report.passes > 0
    assert report.violations == "", f"incomplete rules {report.incomplete}"


# --- accessibility and text size ---------------------------------------------------------------


@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("lang", ["es", "en"])
def test_axe_finds_nothing_loaded_or_failed(
    new_tab: NewTab, server: PanelServer, lang: str, theme: str, width: int
) -> None:
    tab = new_tab(bypass_csp=True, color_scheme=theme, width=width, locale=_locale(lang))
    _log_in(tab, server, lang)
    _open_status(tab, lang)
    for state in ("loaded", "error"):
        if state == "error":
            _abort_status(tab)
            tab.page.click("#status-refresh")
            _wait_checked(tab, lang, "status.checked.old")
        report = tab.axe()
        assert report.passes > 0
        assert report.violations == "", f"{state}: incomplete rules {report.incomplete}"


@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize("lang", ["es", "en"])
def test_at_twice_the_text_size_the_loaded_view_grows_and_nothing_clips(
    new_tab: NewTab, server: PanelServer, lang: str, width: int
) -> None:
    tab = new_tab(width=width, bypass_csp=True, locale=_locale(lang))
    _log_in(tab, server, lang)
    _open_status(tab, lang)

    assert STATUS_SIZES_JS != SIZES_JS  # the walk really is limited to the view
    before = tab.page.evaluate(STATUS_SIZES_JS, True)
    assert len(before) >= 30  # the cards' texts, filled in
    tab.page.add_style_tag(content=TEXT_200_CSS)
    tab.page.evaluate(SETTLE_JS)
    after = tab.page.evaluate(STATUS_SIZES_JS, False)
    shrunk = [
        f"{old['name']}: {old['size']}px -> {new['size']}px"
        for old, new in zip(before, after, strict=True)
        if new["size"] < 1.8 * old["size"]
    ]

    assert shrunk == [], "\n".join(shrunk)
    assert tab.page.evaluate(PAGE_OVERFLOW_JS) <= 0
    assert tab.page.evaluate(CLIPPED_JS) == []
