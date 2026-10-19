"""Rules of the panel's real-browser harness (PR 11c-2a) that hold without a browser.

- No secret value in an assert (the CI console is public): pytest's assertion rewriting prints
  the operands of a failing assert. In tests/browser no assert may touch a token, a cookie list,
  a response's headers or the page's HTML, and no page script may return document.cookie.
- The failure log redactor (tests/browser/redact.py).
- The variables the browser session removes (tests/browser/server.py).
- The test gateway never leaks its socket, thread or HTTP client (tests/browser/server.py).
"""

import ast
import base64
import json
from pathlib import Path
from urllib.parse import quote

import pytest

from tests.browser.console_rules import unexpected_console
from tests.browser.redact import redact
from tests.browser.server import PanelServer, isolated_variable

BROWSER = Path(__file__).resolve().parents[1] / "browser"
# Attributes that hold or reveal a secret: tokens, cookie lists, headers, the page's HTML.
SECRET_ATTRIBUTES = frozenset(
    {
        "admin_token",
        "api_key",
        "throwaway_tokens",
        "cookies",
        "all_headers",
        "header_values",
        "headers",
        "content",
    }
)


def _browser_modules() -> list[Path]:
    modules = sorted(BROWSER.glob("*.py"))
    assert modules
    return modules


@pytest.mark.parametrize("module", _browser_modules(), ids=lambda path: path.name)
def test_no_assert_touches_a_secret(module: Path) -> None:
    tree = ast.parse(module.read_text(encoding="utf-8"))
    found = [
        f"{module.name}:{node.lineno} .{inner.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Assert)
        for inner in ast.walk(node.test)
        if isinstance(inner, ast.Attribute) and inner.attr in SECRET_ATTRIBUTES
    ]
    assert found == []


@pytest.mark.parametrize("module", _browser_modules(), ids=lambda path: path.name)
def test_no_page_script_returns_the_cookie_string(module: Path) -> None:
    text = module.read_text(encoding="utf-8")
    assert "cookie: document.cookie" not in text
    assert "=> document.cookie" not in text


def test_only_the_harness_reads_the_cookie_jar() -> None:
    for module in _browser_modules():
        if module.name != "harness.py":
            assert ".cookies(" not in module.read_text(encoding="utf-8"), module.name


# Where a page may be navigated with page.goto(): only Tab.goto() in the harness, which emulates
# the requested colour scheme again and checks it (review of 11c-3a), and the one helper that
# opens the other site's page (not the gateway's).
GOTO_ALLOWED = {"harness.py": None, "test_panel_ui.py": "_go_to_other_site"}


def page_goto_offenders(name: str, source: str) -> list[str]:
    """Every `<...>.page.goto(` call of a module outside the place it is allowed in."""
    allowed = GOTO_ALLOWED.get(name, "")
    if allowed is None:
        return []

    def calls(tree: ast.AST) -> set[int]:
        return {
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "goto"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "page"
        }

    tree = ast.parse(source)
    inside: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == allowed:
            inside |= calls(node)
    return [f"{name}:{line}" for line in sorted(calls(tree) - inside)]


def test_only_the_harness_navigates_with_page_goto() -> None:
    offenders = [
        offender
        for module in _browser_modules()
        for offender in page_goto_offenders(module.name, module.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_the_goto_rule_sees_an_offender() -> None:
    bad = "def test_x(tab):\n    tab.page.goto('http://127.0.0.1:1/panel')\n"
    assert page_goto_offenders("test_panel_mobile.py", bad) == ["test_panel_mobile.py:2"]
    helper = "def _go_to_other_site(tab, site):\n    tab.page.goto(site.origin)\n"
    assert page_goto_offenders("test_panel_ui.py", helper) == []
    elsewhere = helper + "def test_y(tab):\n    tab.page.goto('x')\n"
    assert page_goto_offenders("test_panel_ui.py", elsewhere) == ["test_panel_ui.py:4"]
    assert page_goto_offenders("harness.py", bad) == []


# --- the redactor ----------------------------------------------------------------------------

# Built, not written: shaped like token_urlsafe(32) (43 characters, with - and _) and like
# token_hex(32) (64 hex digits), without a literal that a secret scanner would flag.
SESSION_ID = base64.urlsafe_b64encode(bytes(range(200, 232))).decode().rstrip("=")
HEX_TOKEN = bytes(range(32)).hex()


def test_the_literal_values_are_replaced_first() -> None:
    assert redact("x short-secret y", ["short-secret"]) == "x <redacted> y"


@pytest.mark.parametrize(
    "text",
    [
        f"cookie antifaz_session={SESSION_ID}; Path=/panel",
        f"GET /panel?c=antifaz_session%3D{SESSION_ID}",
        quote(f"antifaz_session={SESSION_ID}"),
        json.dumps({"value": SESSION_ID}),
        json.dumps(json.dumps({"token": HEX_TOKEN})),  # JSON inside JSON: escaped quotes
        f"token\\u003d{HEX_TOKEN}",
        f"x{HEX_TOKEN}y",
    ],
)
def test_anything_shaped_like_a_token_is_replaced(text: str) -> None:
    redacted = redact(text, [])
    assert SESSION_ID not in redacted
    assert HEX_TOKEN not in redacted
    assert "<redacted>" in redacted


def test_ordinary_text_stays() -> None:
    text = "GET /panel/static/panel.0123456789ab.css 200"
    assert redact(text, []) == text


# --- the isolated session ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "ANTIFAZ_API_KEY",
        "antifaz_admin_token",
        "OPENAI_API_KEY",
        "ANTHROPIC_BASE_URL",
        "PLAYWRIGHT_BROWSERS_PATH",
        "playwright_skip_validate_host_requirements",
        "DEBUG",
        "debug_file",
        "PWDEBUG",
        "HTTP_PROXY",
        "https_proxy",
        "All_Proxy",
    ],
)
def test_the_browser_session_removes(name: str) -> None:
    assert isolated_variable(name)


@pytest.mark.parametrize("name", ["PATH", "NO_PROXY", "LOCALAPPDATA", "DEBUGGER", "MY_DEBUG"])
def test_the_browser_session_keeps(name: str) -> None:
    assert not isolated_variable(name)


# --- the test gateway's cleanup ----------------------------------------------------------------


def test_a_gateway_that_never_started_stops_cleanly() -> None:
    gateway = PanelServer()
    gateway.stop()
    assert gateway.closed


def test_a_started_gateway_stops_and_closes_its_client() -> None:
    gateway = PanelServer()
    try:
        gateway.start()
    finally:
        gateway.stop()
    assert gateway.closed


# --- the one console message a browser may log (tests/browser/console_rules.py) ----------------

BLUETOOTH = "[warning] Error with Permissions-Policy header: Unrecognized feature: 'bluetooth'."


def test_chromium_on_linux_must_log_exactly_the_bluetooth_warning() -> None:
    assert unexpected_console([BLUETOOTH], "chromium", "linux") == []
    assert unexpected_console([BLUETOOTH, BLUETOOTH], "chromium", "linux") == []


def test_on_chromium_linux_the_warning_must_be_there() -> None:
    # When Chromium learns the feature, the exception fails and must be removed.
    assert unexpected_console([], "chromium", "linux") != []


@pytest.mark.parametrize(
    "message",
    [
        "[warning] Error with Permissions-Policy header: Unrecognized feature: 'camera'.",
        BLUETOOTH + " ",
        BLUETOOTH.replace("[warning]", "[error]"),
        "[error] Refused to load the script",
    ],
)
def test_on_chromium_linux_any_other_message_fails(message: str) -> None:
    assert unexpected_console([BLUETOOTH, message], "chromium", "linux") == [message]


@pytest.mark.parametrize(
    ("browser", "platform"),
    [("chromium", "win32"), ("chromium", "darwin"), ("firefox", "linux"), ("webkit", "linux")],
)
def test_elsewhere_the_warning_fails_and_silence_passes(browser: str, platform: str) -> None:
    assert unexpected_console([BLUETOOTH], browser, platform) == [BLUETOOTH]
    assert unexpected_console([], browser, platform) == []


def test_the_page_tests_judge_the_console_with_the_rule() -> None:
    pages = (BROWSER / "test_panel_pages.py").read_text(encoding="utf-8")
    assert "unexpected_console(tab.console, browser_name, sys.platform) == []" in pages
    assert "tab.console == []" not in pages


def test_the_stored_same_site_is_reported_in_the_log() -> None:
    pages = (BROWSER / "test_panel_pages.py").read_text(encoding="utf-8")
    assert "f\"stored sameSite on {browser_name}/{sys.platform}: {cookie['sameSite']}\"" in pages
    conftest = (BROWSER / "conftest.py").read_text(encoding="utf-8")
    assert "def pytest_terminal_summary(" in conftest


REFUSED_401 = (
    "[error] Failed to load resource: the server responded with a status of 401 (Unauthorized)"
)
REFUSED_429 = (
    "[error] Failed to load resource: the server responded with a status of 429 (Too Many Requests)"
)


LOGIN_URL = "http://127.0.0.1:50000/panel/login"


def test_a_refused_login_may_log_its_status_only_as_often_as_the_test_caused_it() -> None:
    """PR 11c-2b: Chromium and WebKit log every 4xx answer to fetch. A test that types a wrong
    token on purpose says how many refusals it caused; only those exact lines, coming from the
    login URL, at most that many times, are allowed (Firefox logs none; none is required)."""
    for browser, platform in (("chromium", "win32"), ("webkit", "linux"), ("firefox", "linux")):
        one = [LOGIN_URL]
        assert unexpected_console([REFUSED_401], browser, platform, one, refused=1) == []
        assert unexpected_console([], browser, platform, [], refused=1) == []
        both = [REFUSED_401, REFUSED_429]
        assert unexpected_console(both, browser, platform, one * 2, refused=2) == []
        assert unexpected_console([REFUSED_401], browser, platform, one) == [REFUSED_401]
        twice = [REFUSED_401, REFUSED_401]
        extra = unexpected_console(twice, browser, platform, one * 2, refused=1)
        assert extra == [REFUSED_401]
    other = REFUSED_401.replace("401 (Unauthorized)", "403 (Forbidden)")
    assert unexpected_console([other], "chromium", "win32", [LOGIN_URL], refused=1) == [other]
    lines = [BLUETOOTH, REFUSED_401]
    assert unexpected_console(lines, "chromium", "linux", ["", LOGIN_URL], refused=1) == []


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:50000/panel/logout",
        "http://127.0.0.1:50000/panel",
        "http://127.0.0.1:50000/panel/login/x",
        "http://127.0.0.1:50000/v1/chat/completions",
        "",
    ],
)
def test_a_refusal_counts_only_when_it_comes_from_the_login(url: str) -> None:
    found = unexpected_console([REFUSED_401], "chromium", "win32", [url], refused=1)
    assert found == [REFUSED_401]


def test_without_locations_no_refusal_is_allowed() -> None:
    assert unexpected_console([REFUSED_401], "chromium", "win32", refused=1) == [REFUSED_401]


def test_linux_is_any_linux_platform_string() -> None:
    assert unexpected_console([BLUETOOTH], "chromium", "linux2") == []
