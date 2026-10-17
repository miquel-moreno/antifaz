"""Today's panel pages in real browsers (issue 53, PR 11c-2a, ADR-0018): Chromium, Firefox, WebKit.

The pages are the placeholders of 11c-1 (GET /panel/login, GET /panel behind the door) and the
two actions of 11b (POST /panel/login, POST /panel/logout), driven from the page itself with
`fetch`, so each browser sends its own Origin and Sec-Fetch-Site through the door. Every test
gets its own gateway on 127.0.0.1 (tests/browser/server.py) and its own browser context; a
request to anything but that gateway is aborted and fails the test (conftest.py).

The CSP is measured in contexts without axe; axe runs in another context with bypass_csp, as
ADR-0018 says, with the axe-core bundled in axe-playwright-python (never a CDN).

No token is ever inside an `assert` expression: a failing assert prints its operands.
"""

import secrets
import sys
import time

import pytest

from tests.browser.harness import NewTab, Tab
from tests.browser.server import PanelServer

pytestmark = pytest.mark.browser

SESSION_COOKIE = "antifaz_session"  # http://127.0.0.1: the local cookie (ADR-0018 addendum)
CSRF_META = 'meta[name="antifaz-csrf"]'
MAX_AGE_SECONDS = 8 * 3600
CANARY_PATH = "/panel/__csp-canary"
# What a browser's cookie store says of SameSite. Playwright's WebKit for Windows (the WinCairo
# port, not Safari) keeps no SameSite in its cookie store and reports "None" for the cookie the
# gateway sent with SameSite=Strict (checked on the Set-Cookie header above, in every browser).
# On that engine the cross-site guard is the door's Origin and Sec-Fetch-Site check and the CSRF
# token (ADR-0018, "LÃ­mite conocido"); Safari itself is checked in 11f on macOS.
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

STORAGE_JS = """() => ({cookie: document.cookie, local: localStorage.length,
                     session: sessionStorage.length})"""

CANARY_PAGE = (
    "<!doctype html><html lang='en'><head><meta charset='utf-8'><title>canary</title></head>"
    "<body><p>canary</p><script>document.title = 'inline script ran';</script></body></html>"
)


def _log_in(tab: Tab, token: str) -> dict[str, object]:
    result: dict[str, object] = tab.page.evaluate(LOGIN_JS, token)
    return result


def _open_panel(tab: Tab, server: PanelServer) -> None:
    """Log in from the login page and open the panel page."""
    tab.goto("/panel/login")
    assert _log_in(tab, server.admin_token)["status"] == 200
    tab.goto("/panel")
    assert tab.page.url == f"{server.origin}/panel"


def _assert_clean(tab: Tab) -> None:
    assert tab.csp_violations() == []
    assert tab.console == []
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
    tab.page.wait_for_function("window.__antifazCsp.length > 0", timeout=30_000)
    assert tab.page.title() == "canary"  # the inline script never ran
    assert any("script-src" in violation for violation in tab.csp_violations())


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
    tab: Tab, server: PanelServer
) -> None:
    tab.goto("/panel/login")
    assert tab.page.locator("h1").inner_text() == "Panel de Antifaz"
    _assert_clean(tab)


def test_the_panel_page_has_no_csp_violation_and_a_clean_console(
    tab: Tab, server: PanelServer
) -> None:
    _open_panel(tab, server)
    assert tab.page.locator(CSRF_META).count() == 1
    _assert_clean(tab)


def test_the_live_dom_has_no_inline_code_and_only_same_origin_requests(
    tab: Tab, server: PanelServer
) -> None:
    tab.goto("/panel/login")
    assert tab.page.evaluate(INLINE_CODE_JS) == []
    _open_panel(tab, server)
    assert tab.page.evaluate(INLINE_CODE_JS) == []
    assert tab.requests  # the check below is not empty
    assert tab.foreign_requests() == []
    assert tab.blocked == []


def test_login_from_the_page_sets_only_the_local_session_cookie(
    tab: Tab, server: PanelServer, browser_name: str
) -> None:
    tab.goto("/panel/login")
    started = time.time()
    with tab.page.expect_response(f"{server.origin}/panel/login") as answer:
        assert _log_in(tab, server.admin_token)["status"] == 200
    # What the gateway sent: the local cookie of ADR-0018 (addendum), in every browser.
    (set_cookie,) = answer.value.header_values("set-cookie")
    attributes = [part.strip() for part in set_cookie.split(";")[1:]]
    assert attributes == ["Max-Age=28800", "Path=/panel", "HttpOnly", "SameSite=Strict"]
    # The browser's own headers got through the door: report what it sent.
    sent = server.door_headers("/panel/login")
    assert sent.get("origin") == server.origin
    assert sent.get("sec-fetch-site") == "same-origin"
    assert "x-antifaz-csrf" not in sent  # the login is exempt from the token layer

    (cookie,) = tab.context.cookies()
    assert cookie["name"] == SESSION_COOKIE
    assert cookie["httpOnly"] is True
    assert cookie["sameSite"] == STORED_SAME_SITE.get((browser_name, sys.platform), "Strict")
    assert cookie["path"] == "/panel"
    assert cookie["secure"] is False
    assert cookie["domain"] == "127.0.0.1"  # host-only: no Domain attribute
    # Max-Age is there (not a session cookie). A wide window: this is not a timing test.
    assert abs(cookie["expires"] - (started + MAX_AGE_SECONDS)) < 3600

    assert tab.page.evaluate(STORAGE_JS) == {"cookie": "", "local": 0, "session": 0}
    tab.goto("/panel")
    assert tab.page.evaluate(STORAGE_JS) == {"cookie": "", "local": 0, "session": 0}
    leaked = tab.shows(server.admin_token)
    assert not leaked, "the admin token is in the DOM, a URL or the console"


def test_a_wrong_token_gets_401_and_no_cookie(tab: Tab, server: PanelServer) -> None:
    tab.goto("/panel/login")
    assert _log_in(tab, secrets.token_hex(32))["status"] == 401
    assert tab.context.cookies() == []
    tab.goto("/panel")
    assert tab.page.url == f"{server.origin}/panel/login"
    assert tab.foreign_requests() == []
    assert tab.blocked == []


def test_too_many_failures_get_429_with_a_positive_retry_after(
    tab: Tab, server: PanelServer
) -> None:
    """The limit of ADR-0018 (5 failures a minute per client), on this test's own gateway."""
    tab.goto("/panel/login")
    statuses = [_log_in(tab, secrets.token_hex(32))["status"] for _ in range(5)]
    assert statuses == [401] * 5
    blocked = _log_in(tab, secrets.token_hex(32))
    assert blocked["status"] == 429
    assert int(str(blocked["retryAfter"])) > 0
    assert tab.context.cookies() == []


def test_logout_from_the_page_drops_the_cookie_and_the_panel(tab: Tab, server: PanelServer) -> None:
    _open_panel(tab, server)
    # Browsers keep Clear-Site-Data from the page's JavaScript: read it from the network.
    with tab.page.expect_response(f"{server.origin}/panel/logout") as answer:
        assert tab.page.evaluate(LOGOUT_JS) == 200
    assert answer.value.all_headers().get("clear-site-data") == '"cache"'
    assert server.door_headers("/panel/logout") == {
        "origin": server.origin,
        "sec-fetch-site": "same-origin",
        "x-antifaz-csrf": "present",
    }
    assert tab.context.cookies() == []
    tab.goto("/panel")
    assert tab.page.url == f"{server.origin}/panel/login"
    tab.page.go_back(wait_until="load", timeout=30_000)
    assert tab.page.locator(CSRF_META).count() == 0
    assert "Has entrado" not in tab.page.content()
    assert tab.blocked == []


@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_axe_finds_no_violation(
    new_tab: NewTab, server: PanelServer, theme: str, width: int
) -> None:
    tab = new_tab(bypass_csp=True, color_scheme=theme, width=width)
    tab.goto("/panel/login")
    assert tab.axe() == ""
    _open_panel(tab, server)
    assert tab.axe() == ""
    assert tab.blocked == []
