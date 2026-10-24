"""The session expiry warning in real browsers (issue 53, PR 11d-b): Chromium, Firefox, WebKit.

Playwright's clock API drives the page's time (installed before the login, so session.js reads
the fake clock from its first line); the gateway's own clock is real, so its session stays alive.

- About two minutes before the inactivity limit a banner says when the session expires and
  offers «Seguir»: in the flow at the top of the content (it covers nothing), a polite status (not
  an alert) and the focus never moves. Nothing asks the server until the button is pressed; the
  press extends the inactivity part and the banner goes.
- Near the 8 hours there is no «Seguir»: the text says when the session ends.
- At the limit the text and the results are wiped and the page goes to the login (with ?lang); a
  page hidden past the limit does it as soon as it is visible again; a 401 from a Try does it too.
- Every panel answer that was a use (the Status view here) moves the inactivity limit.
- A failed «Seguir» says so and leaves the button usable. axe finds nothing with the banner.
"""

import contextlib
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Route

from antifaz.web.i18n import load_catalogs
from antifaz.web.sessions import IDLE_SECONDS, MAX_AGE_SECONDS
from tests.browser.harness import TIMEOUT_MS, NewTab, Tab
from tests.browser.server import PanelServer, ServerOptions
from tests.browser.test_panel_try import ACTIVE_ID_JS, axe_clean, clean, log_in, text_of

pytestmark = pytest.mark.browser

CATALOGS = load_catalogs()
SESSION_PATH = "/panel/api/session"
# The warning comes 2 minutes before the end the page keeps, which is 5 s (session.js's
# safety margin, review 1) before the server's.
WARN_SECONDS = 120 + 5
SHOWN_JS = "() => document.getElementById('session-warning').childElementCount > 0"
GONE_JS = "() => document.getElementById('session-warning').childElementCount === 0"
CONTINUE = "#session-warning [data-continue]"
VISIBILITY_JS = """(state) => {
  Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => state});
  Object.defineProperty(document, 'hidden', {configurable: true, get: () => state === 'hidden'});
  document.dispatchEvent(new Event('visibilitychange'));
}"""
# Where the banner is: in the flow of the content (not fixed or absolute), above the view, and
# the view's title is still the element at its own centre (nothing covers it).
LAYOUT_JS = """() => {
  const box = document.getElementById('session-warning');
  const title = document.querySelector('#panel-playground h1');
  const b = box.getBoundingClientRect(), t = title.getBoundingClientRect();
  const hit = document.elementFromPoint(t.left + t.width / 2, t.top + t.height / 2);
  return {position: getComputedStyle(box).position, above: b.bottom <= t.top,
          covered: !(hit === title || title.contains(hit)), inMain: !!box.closest('main')};
}"""
NOW_JS = "() => Date.now()"


@pytest.fixture
def server_options() -> ServerOptions:
    return ServerOptions(ner_names=True)


def _t(key: str, lang: str = "es") -> str:
    return CATALOGS[lang].text(key)


def _prefix(key: str) -> str:
    return _t(key).replace("{hours}", str(MAX_AGE_SECONDS // 3600)).split("{time}", 1)[0]


def _session_requests(tab: Tab) -> int:
    return len([url for url in tab.requests if urlsplit(url).path == SESSION_PATH])


def _start(tab: Tab, server: PanelServer) -> None:
    tab.page.clock.install()
    log_in(tab, server)


def test_the_warning_comes_before_the_limit_and_continue_extends(
    tab: Tab, server: PanelServer, browser_name: str
) -> None:
    _start(tab, server)
    tab.page.focus("#try-text")
    tab.page.keyboard.type("hola")

    tab.page.clock.run_for((IDLE_SECONDS - WARN_SECONDS - 5) * 1000)
    assert tab.page.evaluate(GONE_JS)
    tab.page.clock.run_for(10 * 1000)
    tab.page.wait_for_function(SHOWN_JS, timeout=TIMEOUT_MS)

    warning = text_of(tab, "session-warning")
    assert _t("session.warn.title") in warning and _prefix("session.warn.idle") in warning
    assert re.search(r"\d{2}:\d{2}", warning)
    assert tab.page.get_attribute("#session-warning", "role") == "status"
    assert tab.page.locator("#session-warning [role=alert]").count() == 0
    assert tab.page.evaluate(ACTIVE_ID_JS) == "try-text"  # the focus never moved
    layout = tab.page.evaluate(LAYOUT_JS)
    assert layout == {"position": "static", "above": True, "covered": False, "inMain": True}
    assert _session_requests(tab) == 0  # showing it asked nothing
    axe_clean(tab)

    with tab.page.expect_response(lambda r: urlsplit(r.url).path == SESSION_PATH) as answer:
        tab.page.click(CONTINUE)
    assert answer.value.status == 200
    tab.page.wait_for_function(GONE_JS, timeout=TIMEOUT_MS)
    assert tab.page.input_value("#try-text") == "hola"

    tab.page.clock.run_for((IDLE_SECONDS - WARN_SECONDS - 5) * 1000)
    assert tab.page.evaluate(GONE_JS)  # the limit moved with the press
    tab.page.clock.run_for(10 * 1000)
    tab.page.wait_for_function(SHOWN_JS, timeout=TIMEOUT_MS)
    assert _session_requests(tab) == 1
    clean(tab, browser_name)


def test_nothing_asks_the_server_by_itself(tab: Tab, server: PanelServer) -> None:
    _start(tab, server)
    before = len(tab.requests)

    tab.page.clock.run_for((IDLE_SECONDS - WARN_SECONDS - 10) * 1000)

    assert tab.requests[before:] == []


def test_at_the_limit_the_text_goes_and_the_page_goes_to_the_login(
    tab: Tab, server: PanelServer
) -> None:
    _start(tab, server)
    tab.page.fill("#try-text", "Escribe a lola.inventada@example.org")

    tab.page.clock.run_for((IDLE_SECONDS + 1) * 1000)
    tab.page.wait_for_url(f"{server.origin}/panel/login?lang=es")

    tab.page.go_back(wait_until="load")
    page = tab.page.content()
    assert "lola.inventada" not in page


def test_near_the_8_hours_there_is_no_continue(tab: Tab, server: PanelServer) -> None:
    """The page's absolute end is rewritten to 10 minutes from now (the server's real clock has
    the 8 hours ahead): the warning says when it ends and offers nothing."""

    def shorter(route: Route) -> None:
        response = route.fetch()
        body = re.sub(
            r'<meta name="antifaz-session-max" content="\d+">',
            '<meta name="antifaz-session-max" content="600">',
            response.text(),
        )
        route.fulfill(response=response, body=body)

    tab.page.clock.install()
    tab.page.route(re.compile(r".*/panel\?lang=es$"), shorter)
    log_in(tab, server)

    tab.page.clock.run_for((600 - WARN_SECONDS + 5) * 1000)
    tab.page.wait_for_function(SHOWN_JS, timeout=TIMEOUT_MS)

    assert _prefix("session.warn.max") in text_of(tab, "session-warning")
    assert tab.page.locator(CONTINUE).count() == 0
    axe_clean(tab)
    tab.page.clock.run_for(WARN_SECONDS * 1000)
    tab.page.wait_for_url(f"{server.origin}/panel/login?lang=es")


def test_a_page_hidden_past_the_limit_expires_when_it_is_shown(
    tab: Tab, server: PanelServer
) -> None:
    _start(tab, server)
    tab.page.evaluate(VISIBILITY_JS, "hidden")
    now = tab.page.evaluate(NOW_JS)

    # The system time jumps past the limit without firing any timer (a throttled background tab).
    tab.page.clock.set_system_time(datetime.fromtimestamp(now / 1000 + IDLE_SECONDS + 60, tz=UTC))
    assert tab.page.url.endswith("/panel?lang=es")
    tab.page.evaluate(VISIBILITY_JS, "visible")

    tab.page.wait_for_url(f"{server.origin}/panel/login?lang=es")


def test_a_401_from_a_try_goes_to_the_login(tab: Tab, server: PanelServer) -> None:
    log_in(tab, server)
    tab.page.route(
        f"{tab.origin}/panel/api/try",
        lambda route: route.fulfill(
            status=401, headers={"Content-Type": "application/json"}, body="{}"
        ),
    )
    tab.page.fill("#try-text", "hola")

    tab.page.click("#try-submit")

    tab.page.wait_for_url(f"{server.origin}/panel/login?lang=es")


def test_a_panel_answer_moves_the_inactivity_limit(tab: Tab, server: PanelServer) -> None:
    _start(tab, server)
    tab.page.clock.run_for(20 * 60 * 1000)
    with tab.page.expect_response(lambda r: urlsplit(r.url).path == "/panel/api/status"):
        tab.page.click("#tab-status")
    # The page has handled the answer (panel.js reports the use before it fills the view).
    tab.page.wait_for_function(
        "() => document.getElementById('status-refresh').getAttribute('aria-disabled') !== 'true'",
        timeout=TIMEOUT_MS,
    )

    tab.page.clock.run_for(9 * 60 * 1000)  # 29 minutes after the login, 9 after the last use
    assert tab.page.evaluate(GONE_JS)
    tab.page.clock.run_for(20 * 60 * 1000)
    tab.page.wait_for_function(SHOWN_JS, timeout=TIMEOUT_MS)


def test_the_page_expires_a_little_before_the_server(tab: Tab, server: PanelServer) -> None:
    """Review 1: a safety margin of a few seconds, so the page never shows a session the server
    has already closed."""
    _start(tab, server)

    tab.page.clock.run_for((IDLE_SECONDS - 30) * 1000)
    assert tab.page.url.endswith("/panel?lang=es")
    tab.page.clock.run_for(26 * 1000)  # four seconds before the server's limit

    # The installed clock keeps running with real time: 1.5 s more is still before the limit.
    tab.page.wait_for_url(f"{server.origin}/panel/login?lang=es", timeout=1500)


@pytest.mark.parametrize(("status", "counts"), [(500, False), (503, False), (400, True)])
def test_only_answers_the_door_handled_move_the_limit(
    tab: Tab, server: PanelServer, status: int, counts: bool
) -> None:
    """Review 1: a 2xx or a 4xx (not 401 or 403) moved the server's limit; a 5xx is not counted."""
    _start(tab, server)
    tab.page.route(
        f"{tab.origin}/panel/api/try",
        lambda route: route.fulfill(
            status=status, headers={"Content-Type": "application/json"}, body="{}"
        ),
    )
    tab.page.clock.run_for(20 * 60 * 1000)
    tab.page.fill("#try-text", "hola")
    tab.page.click("#try-submit")
    # Polled from here: with the clock installed, Playwright's own waits use fake timers.
    give_up = time.monotonic() + TIMEOUT_MS / 1000
    while not tab.page.evaluate("() => !document.getElementById('try-error').hidden"):
        assert time.monotonic() < give_up, "the answer never showed"
        time.sleep(0.05)

    tab.page.clock.run_for(9 * 60 * 1000)  # 29 minutes after the login, 9 after the answer
    shown = tab.page.evaluate(SHOWN_JS)

    assert shown is not counts  # counted: the warning is still far; not counted: it is due


def test_a_failed_continue_says_so_and_can_be_pressed_again(tab: Tab, server: PanelServer) -> None:
    _start(tab, server)
    tab.page.clock.run_for((IDLE_SECONDS - WARN_SECONDS + 5) * 1000)
    tab.page.wait_for_function(SHOWN_JS, timeout=TIMEOUT_MS)
    tab.page.route(
        f"{tab.origin}{SESSION_PATH}",
        lambda route: route.fulfill(status=500, headers={"Content-Type": "application/json"}),
    )

    tab.page.click(CONTINUE)
    tab.page.wait_for_function(
        "(text) => document.getElementById('session-warning').textContent.includes(text)",
        arg=_t("session.warn.failed"),
        timeout=TIMEOUT_MS,
    )

    assert tab.page.get_attribute(CONTINUE, "aria-disabled") is None


@pytest.mark.parametrize("lang", ["es", "en"])
def test_on_a_phone_the_banner_stays_in_the_flow(
    new_tab: NewTab, server: PanelServer, lang: str
) -> None:
    tab = new_tab(width=390, locale="es-ES" if lang == "es" else "en-US")
    tab.page.clock.install()
    log_in(tab, server, lang)

    tab.page.clock.run_for((IDLE_SECONDS - WARN_SECONDS + 5) * 1000)
    tab.page.wait_for_function(SHOWN_JS, timeout=TIMEOUT_MS)

    assert CATALOGS[lang].text("session.warn.title") in text_of(tab, "session-warning")
    assert tab.page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth") <= 0
    assert tab.page.evaluate(LAYOUT_JS)["covered"] is False
    axe_clean(tab)


SECOND_NAVIGATION_WINDOW_MS = 2_000


def _until(check: Callable[[], bool], tab: Tab, what: str) -> None:
    """Let Playwright deliver its events until `check()` (bounded: a hang guard only). Nothing
    is evaluated in the page: it may be in the middle of a navigation."""
    deadline = time.monotonic() + TIMEOUT_MS / 1000
    while not check():
        assert time.monotonic() < deadline, what
        tab.page.wait_for_timeout(20)


def test_the_expiry_and_a_late_401_leave_for_the_login_only_once(
    tab: Tab, server: PanelServer
) -> None:
    """Review 1 of 11e-2: session.js leaves at the expiry while a request of panel.js («Estado»)
    is still pending; when that request then answers 401, panel.js must not leave a second time
    (the second navigation would abort the first). The login page's document is held until the
    late answer has been handled, and every request for it is counted."""
    held_status: list[Route] = []
    held_login: list[Route] = []
    logins: list[str] = []
    _start(tab, server)
    tab.page.route(f"{tab.origin}/panel/api/status", lambda route: held_status.append(route))
    tab.page.route(f"{tab.origin}/panel/login*", lambda route: held_login.append(route))
    tab.page.on(
        "request",
        lambda request: (
            logins.append(request.url)
            if request.resource_type == "document" and urlsplit(request.url).path == "/panel/login"
            else None
        ),
    )
    try:
        tab.page.click("#tab-status")
        _until(lambda: len(held_status) == 1, tab, "the status request is held")

        tab.page.clock.run_for((IDLE_SECONDS + 1) * 1000)  # session.js leaves: login held
        _until(lambda: len(held_login) == 1, tab, "the expiry asked for the login")
        # Firefox cancels the page's pending fetch when the navigation starts (so the late 401
        # never reaches panel.js there); Chromium and WebKit deliver it.
        with contextlib.suppress(PlaywrightError):
            held_status.pop().fulfill(
                status=401,
                headers={"Content-Type": "application/json"},
                body='{"error": {"code": "unauthorized"}}',
            )
        # The page is navigating: nothing can be evaluated in it any more. A second navigation
        # would start right after the 401 reaches panel.js: a bounded window to see it.
        tab.page.wait_for_timeout(SECOND_NAVIGATION_WINDOW_MS)

        assert len(logins) == 1, "a second navigation to the login"
        held_login.pop().continue_()
        tab.page.wait_for_url(f"{server.origin}/panel/login*", wait_until="load")
    finally:  # never leave a request held: the context would wait for it
        for route in [*held_status, *held_login]:
            with contextlib.suppress(PlaywrightError):
                route.abort()
