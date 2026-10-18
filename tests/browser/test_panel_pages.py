"""Today's panel pages in real browsers (issue 53, PR 11c-2a, ADR-0018): Chromium, Firefox, WebKit.

The pages (GET /panel/login, GET /panel behind the door; their interface since 11c-2b, see
test_panel_ui.py) and the two actions of 11b (POST /panel/login, POST /panel/logout), driven
from the page itself with `fetch`, so each browser sends its own Origin and Sec-Fetch-Site
through the door. Every test gets its own gateway on 127.0.0.1 (tests/browser/server.py) and
its own browser context; a request to anything but that gateway is aborted and fails the test
(conftest.py).

The CSP is measured in contexts without axe, and every panel page is checked to have really
come with the panel's CSP header; axe runs in another context (ADR-0018), with bypass_csp so
the injected script is not blocked, using the axe-core bundled in axe-playwright-python (never
a CDN).

No token, session id or CSRF value is ever inside an `assert` expression: a failing assert
prints its operands, and the CI console is public. Cookies are seen by name and attributes
only (tests/unit/test_browser_harness_rules.py checks it).
"""

import secrets
import sys
import time

import pytest

from antifaz.web.headers import CONTENT_SECURITY_POLICY
from antifaz.web.i18n import load_catalogs
from tests.browser.console_rules import unexpected_console
from tests.browser.harness import TIMEOUT_MS, NewTab, Tab
from tests.browser.server import PanelServer

pytestmark = pytest.mark.browser

ENGLISH = load_catalogs()["en"]

SESSION_COOKIE = "antifaz_session"  # http://127.0.0.1: the local cookie (ADR-0018 addendum)
CSRF_META = 'meta[name="antifaz-csrf"]'
CANARY_PATH = "/panel/__csp-canary"
NO_CSP_PATH = "/panel/__no-csp"
# What a browser's cookie store says of SameSite. Playwright's WebKit for Windows (the WinCairo
# port, not Safari) keeps no SameSite in its cookie store and reports "None" for the cookie the
# gateway sent with SameSite=Strict (checked on the Set-Cookie header, in every browser). On
# that engine the cross-site guard is the door's Origin and Sec-Fetch-Site check and the CSRF
# token (ADR-0018, "Límite conocido"); Safari itself is checked in 11f on macOS.
STORED_SAME_SITE = {("webkit", "win32"): "None"}

LOGIN_JS = """async (token) => {
  const r = await fetch('/panel/login', {
    method: 'POST', credentials: 'same-origin', cache: 'no-store',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify({token}),
  });
  return {status: r.status, retryAfter: r.headers.get('Retry-After')};
}"""

LOGOUT_JS = """async () => {
  const meta = document.querySelector('meta[name="antifaz-csrf"]');
  const r = await fetch('/panel/logout', {
    method: 'POST', credentials: 'same-origin', cache: 'no-store',
    headers: {'Content-Type': 'application/json', 'X-Antifaz-CSRF': meta.content}, body: '{}',
  });
  return r.status;
}"""

# Everything the CSP would block if it were there: scripts without src, <style>, style="..."
# and on...= attributes, in the live DOM (after any script ran).
INLINE_CODE_JS = """() => {
  const out = [];
  for (const s of document.scripts) if (!s.src) out.push('script without src');
  for (const s of document.querySelectorAll('style')) out.push('<style>');
  for (const el of document.querySelectorAll('*')) {
    for (const a of el.attributes) {
      if (a.name === 'style') out.push('style= on <' + el.tagName.toLowerCase() + '>');
      if (a.name.startsWith('on')) out.push(a.name + '= on <' + el.tagName.toLowerCase() + '>');
    }
  }
  return out;
}"""

# Whether the page can see a cookie or holds anything in storage: never the values.
STORAGE_JS = """() => ({cookieVisible: document.cookie !== '', local: localStorage.length,
                     session: sessionStorage.length})"""
NOTHING_STORED = {"cookieVisible": False, "local": 0, "session": 0}

CANARY_PAGE = (
    "<!doctype html><html lang='en'><head><meta charset='utf-8'><title>canary</title></head>"
    "<body><p>canary</p><script>document.title = 'inline script ran';</script></body></html>"
)
PLAIN_PAGE = "<!doctype html><html lang='en'><head><title>plain</title></head><body></body></html>"


def _log_in(tab: Tab, token: str) -> dict[str, object]:
    result: dict[str, object] = tab.page.evaluate(LOGIN_JS, token)
    return result


def _goto_panel_page(tab: Tab, path: str) -> None:
    """Open a page of the panel and check that the browser got the panel's own CSP with it."""
    tab.goto(path)
    assert tab.csp_header() == CONTENT_SECURITY_POLICY


def _open_panel(tab: Tab, server: PanelServer) -> None:
    """Log in from the login page and open the panel page."""
    _goto_panel_page(tab, "/panel/login")
    status = _log_in(tab, server.admin_token)["status"]
    assert status == 200
    _goto_panel_page(tab, "/panel")
    assert tab.page.url == f"{server.origin}/panel"


def _assert_clean(tab: Tab, browser_name: str) -> None:
    assert tab.csp_violations() == []
    # Nothing in the console, but for the one strict exception of console_rules.py.
    assert unexpected_console(tab.console, browser_name, sys.platform) == []
    assert tab.errors == []
    assert tab.failed == []
    assert tab.blocked == []
    assert tab.foreign_requests() == []


def test_the_csp_detector_catches_an_inline_script(tab: Tab, server: PanelServer) -> None:
    """The canary: the panel's real CSP on a same-origin page with an inline <script>. The
    detector must see the violation in every browser, or `csp_violations() == []` proves
    nothing."""
    tab.serve_canary(CANARY_PATH, CANARY_PAGE)
    tab.goto(CANARY_PATH)
    tab.page.wait_for_function("window.__antifazCsp.length > 0", timeout=TIMEOUT_MS)
    assert tab.page.title() == "canary"  # the inline script never ran
    assert any("script-src" in violation for violation in tab.csp_violations())
    assert tab.csp_header() == CONTENT_SECURITY_POLICY


def test_the_csp_header_check_sees_a_page_without_it(tab: Tab, server: PanelServer) -> None:
    """The header check is not empty: a same-origin page served without CSP is told apart."""
    tab.serve(NO_CSP_PATH, PLAIN_PAGE, {})
    tab.goto(NO_CSP_PATH)
    assert tab.page.title() == "plain"
    assert tab.csp_header() is None


def test_a_request_outside_the_gateway_is_aborted_and_recorded(new_tab: NewTab) -> None:
    """The other canary: the context route stops a foreign request before the network (an
    address of TEST-NET-1, RFC 5737) and records it, or `blocked == []` proves nothing. The
    CSP is bypassed here only so that the request reaches the route at all."""
    foreign = "http://192.0.2.1/canary.png"
    tab = new_tab(bypass_csp=True)
    tab.goto("/panel/login")
    tab.page.evaluate("(url) => fetch(url, {mode: 'no-cors'}).catch(() => null)", foreign)
    assert tab.blocked == [foreign]
    assert tab.foreign_requests() == [foreign]
    tab.blocked.clear()  # expected here; the fixture fails any other test that has one


def test_the_login_page_has_no_csp_violation_and_a_clean_console(
    tab: Tab, server: PanelServer, browser_name: str
) -> None:
    _goto_panel_page(tab, "/panel/login")
    assert tab.page.locator("h1").inner_text() == ENGLISH.text("login.heading")  # en-US
    _assert_clean(tab, browser_name)


def test_the_panel_page_has_no_csp_violation_and_a_clean_console(
    tab: Tab, server: PanelServer, browser_name: str
) -> None:
    _open_panel(tab, server)
    assert tab.page.locator(CSRF_META).count() == 1
    _assert_clean(tab, browser_name)


def test_the_live_dom_has_no_inline_code_and_only_same_origin_requests(
    tab: Tab, server: PanelServer
) -> None:
    _goto_panel_page(tab, "/panel/login")
    assert tab.page.evaluate(INLINE_CODE_JS) == []
    _open_panel(tab, server)
    assert tab.page.evaluate(INLINE_CODE_JS) == []
    assert tab.requests  # the check below is not empty
    assert tab.foreign_requests() == []
    assert tab.blocked == []
    assert tab.csp_violations() == []


def test_login_from_the_page_sets_only_the_local_session_cookie(
    tab: Tab, server: PanelServer, browser_name: str, panel_notes: list[str]
) -> None:
    _goto_panel_page(tab, "/panel/login")
    started = time.time()
    with tab.page.expect_response(f"{server.origin}/panel/login") as answer:
        status = _log_in(tab, server.admin_token)["status"]
        assert status == 200
    # What the gateway sent: the local cookie of ADR-0018 (addendum), in every browser. Only
    # the name and the attributes are compared, never the value.
    (set_cookie,) = answer.value.header_values("set-cookie")
    name = set_cookie.split("=", 1)[0]
    attributes = [part.strip() for part in set_cookie.split(";")[1:]]
    assert name == SESSION_COOKIE
    assert attributes == ["Max-Age=28800", "Path=/panel", "HttpOnly", "SameSite=Strict"]
    # The browser's own headers got through the door: report what it sent.
    sent = server.door_headers("/panel/login")
    assert sent.get("origin") == server.origin
    assert sent.get("sec-fetch-site") == "same-origin"
    assert "x-antifaz-csrf" not in sent  # the login is exempt from the token layer

    (cookie,) = tab.cookie_attributes()
    # One line in the terminal summary (the CI log): only this attribute, never a value.
    panel_notes.append(f"stored sameSite on {browser_name}/{sys.platform}: {cookie['sameSite']}")
    assert cookie["name"] == SESSION_COOKIE
    assert cookie["httpOnly"] is True
    assert cookie["sameSite"] == STORED_SAME_SITE.get((browser_name, sys.platform), "Strict")
    assert cookie["path"] == "/panel"
    assert cookie["secure"] is False
    assert cookie["domain"] == "127.0.0.1"  # host-only: no Domain attribute
    assert cookie["expires"] > started  # Max-Age made it a persistent cookie (exact value above)

    assert tab.page.evaluate(STORAGE_JS) == NOTHING_STORED
    _goto_panel_page(tab, "/panel")
    assert tab.page.evaluate(STORAGE_JS) == NOTHING_STORED
    leaked = tab.shows(server.admin_token)
    assert not leaked, "the admin token is in the DOM, a URL or the console"
    assert tab.csp_violations() == []


def test_a_wrong_token_gets_401_and_no_cookie(tab: Tab, server: PanelServer) -> None:
    _goto_panel_page(tab, "/panel/login")
    assert _log_in(tab, secrets.token_hex(32))["status"] == 401
    assert tab.cookie_names() == []
    _goto_panel_page(tab, "/panel")
    assert tab.page.url == f"{server.origin}/panel/login"
    assert tab.foreign_requests() == []
    assert tab.blocked == []


def test_too_many_failures_get_429_with_a_positive_retry_after(
    tab: Tab, server: PanelServer
) -> None:
    """The limit of ADR-0018 (5 failures a minute per client), on this test's own gateway.

    It relies on the limiter's real 60 s window: the six attempts must land inside one minute.
    There is no fake clock to inject without changing src/, and six fetches on loopback take a
    small fraction of that, so this is not a timing assertion."""
    _goto_panel_page(tab, "/panel/login")
    statuses = [_log_in(tab, secrets.token_hex(32))["status"] for _ in range(5)]
    assert statuses == [401] * 5
    blocked = _log_in(tab, secrets.token_hex(32))
    assert blocked["status"] == 429
    assert int(str(blocked["retryAfter"])) > 0
    assert tab.cookie_names() == []


def test_logout_from_the_page_drops_the_cookie_and_the_panel(tab: Tab, server: PanelServer) -> None:
    _open_panel(tab, server)
    # Browsers keep Clear-Site-Data from the page's JavaScript: read it from the network.
    with tab.page.expect_response(f"{server.origin}/panel/logout") as answer:
        assert tab.page.evaluate(LOGOUT_JS) == 200
    clear_site_data = answer.value.header_value("clear-site-data")
    assert clear_site_data == '"cache"'
    assert server.door_headers("/panel/logout") == {
        "origin": server.origin,
        "sec-fetch-site": "same-origin",
        "x-antifaz-csrf": "present",
    }
    assert tab.cookie_names() == []
    _goto_panel_page(tab, "/panel")
    assert tab.page.url == f"{server.origin}/panel/login"
    tab.page.go_back(wait_until="load", timeout=TIMEOUT_MS)
    assert tab.page.locator(CSRF_META).count() == 0
    assert tab.page.locator('[role="tablist"]').count() == 0, "the panel came back after logout"
    assert tab.blocked == []
    assert tab.csp_violations() == []


@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("locale", ["es-ES", "en-US"])
def test_axe_finds_no_violation(
    new_tab: NewTab, server: PanelServer, theme: str, width: int, locale: str
) -> None:
    """Both pages in both languages (11c-2b: the browser's locale picks the page's)."""
    tab = new_tab(bypass_csp=True, color_scheme=theme, width=width, locale=locale)
    _goto_panel_page(tab, "/panel/login")
    for page in ("login", "panel"):
        if page == "panel":
            _open_panel(tab, server)
        report = tab.axe()
        assert report.passes > 0  # axe really ran its rules on this page
        assert report.violations == "", f"{page}: incomplete rules {report.incomplete}"
    assert tab.blocked == []
