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
