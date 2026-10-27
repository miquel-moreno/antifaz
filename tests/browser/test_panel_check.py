"""«Comprobar ahora», the header pill and the Estado tab's dot in real browsers (issue 53,
PR 11e-4, ADR-0018): Chromium, Firefox and WebKit.

The gateway of these tests has the invented provider of tests/browser/server.py IN THIS PROCESS
(`fake_provider`): GET .../models answers as `check_mode` says (ok, 401, 302, timeout, connect,
mixed). The real providers are never reached (the fixture checks `provider_calls` is empty) and
each press asks the invented provider exactly twice (one model list per provider).

- At load: the pill (desktop header or phone bar: both in the page, only one shown) and the dot
  come from the server's local overall (ok, warn, danger); no request at all to /panel/api/*, and
  none either with the page's clock moved 27 minutes on (no timer).
- A failed status read: the pill says «Sin actualizar» (neutral) and the dot hides.
- «Comprobar ahora»: both buttons aria-disabled (never disabled) while it checks, the focus stays
  on the button, the live line says «Comprobando los proveedores…» then «Proveedores comprobados a
  las …», each provider row says its result; a refused or failed provider never changes the pill.
- 429: «Espera {s} s para volver a comprobar.» (no countdown); 503: «Ya hay una comprobación en
  marcha»; 401: the login, with ?lang.
- A late status answer never overwrites the newer snapshot of a check.
- CSP clean, console clean, no foreign request; axe finds nothing (es/en, light/dark, desktop and
  phone, after a mixed check); forced colours keep the pill's word and the dot; at 200 % text the
  checked view grows and nothing clips or spills.
"""

import re
import sys
from typing import Any

import pytest
from playwright.sync_api import Response, Route

from antifaz.web.i18n import load_catalogs
from tests.browser.console_rules import unexpected_console
from tests.browser.harness import TIMEOUT_MS, NewTab, Tab
from tests.browser.server import PanelServer, ServerOptions
from tests.browser.test_panel_status import (
    CLIPPED_JS,
    PAGE_OVERFLOW_JS,
    ROUND_TRIPS,
    STATUS_PATH,
    STATUS_SIZES_JS,
    _answer,
)
from tests.browser.test_panel_text_size import SETTLE_JS, TEXT_200_CSS

pytestmark = pytest.mark.browser

CATALOGS = load_catalogs()
CHECK_PATH = "/panel/api/status/check"
API_PREFIX = "/panel/api/"
VISIBLE_PILL_JS = """() => {
  const all = [...document.querySelectorAll('[data-app-status]')];
  const shown = all.filter((p) => p.getClientRects().length > 0 &&
    getComputedStyle(p).visibility !== 'hidden');
  return {count: all.length, shown: shown.length,
          label: shown.length ? shown[0].querySelector('[data-label]').textContent : null,
          labels: all.map((p) => p.querySelector('[data-label]').textContent),
          tones: all.map((p) => ['ok', 'warn', 'danger', 'neutral']
            .filter((t) => p.classList.contains('is-' + t))),
          overall: all.map((p) => p.getAttribute('data-overall')),
          text: shown.length ? shown[0].textContent : null};
}"""
DOT_JS = """() => { const d = document.getElementById('status-dot');
  return {hidden: d.hidden, shown: d.getClientRects().length > 0,
          tones: ['warn', 'danger'].filter((t) => d.classList.contains('is-' + t)),
          text: d.textContent}; }"""
TAB_NAME_JS = "() => document.getElementById('tab-status').textContent"
ROWS_JS = """() => [...document.querySelectorAll('[data-provider]')].map((row) => {
  const line = row.querySelector('[data-check]');
  return {id: row.getAttribute('data-provider'), hidden: line.hidden,
          text: line.querySelector('[data-label]').textContent};
})"""
CHECK_IDLE_JS = """() =>
  document.getElementById('status-check').getAttribute('aria-disabled') !== 'true'"""
LINE_STARTS_JS = """(prefix) =>
  document.getElementById('check-line').textContent.startsWith(prefix)"""
PRESS_CHECK_AND_READ_JS = """() => {
  const check = document.getElementById('status-check');
  const refresh = document.getElementById('status-refresh');
  check.focus();
  check.click();
  return {check: check.getAttribute('aria-disabled'),
          refresh: refresh.getAttribute('aria-disabled'),
          disabled: check.disabled || refresh.disabled,
          line: document.getElementById('check-line').textContent,
          focused: document.activeElement === check};
}"""
ACTIVE_ID_JS = "() => document.activeElement ? document.activeElement.id : null"
STATUS_IDLE_JS = """() =>
  document.getElementById('status-refresh').getAttribute('aria-disabled') !== 'true' &&
  document.getElementById('status-time').textContent !== ''"""


@pytest.fixture
def server_options() -> ServerOptions:
    return ServerOptions(fake_provider=True)


def _locale(lang: str) -> str:
    return "es-ES" if lang == "es" else "en-US"


def _text(lang: str, key: str) -> str:
    return CATALOGS[lang].text(key)


def _log_in(tab: Tab, server: PanelServer, lang: str) -> None:
    tab.goto(f"/panel/login?lang={lang}")
    tab.page.fill("#login-token", server.admin_token)
    tab.page.click("#login-form button[type=submit]")
    tab.page.wait_for_url(f"{server.origin}/panel?lang={lang}")
    tab.page.evaluate(SETTLE_JS)
    tab.remember_secrets()


def _api_requests(tab: Tab) -> list[str]:
    return [url for url in tab.requests if API_PREFIX in url and "/panel/api/live" not in url]


def _is_check(response: Response) -> bool:
    return response.url.split("?", 1)[0].endswith(CHECK_PATH)


def _is_status(response: Response) -> bool:
    return response.url.split("?", 1)[0].endswith(STATUS_PATH)


def _open_status(tab: Tab) -> None:
    with tab.page.expect_response(_is_status):
        tab.page.click("#tab-status")
    tab.page.wait_for_function(STATUS_IDLE_JS, timeout=TIMEOUT_MS)


def _press_check(tab: Tab, lang: str, line_key: str = "status.check.checked") -> None:
    with tab.page.expect_response(_is_check):
        tab.page.click("#status-check")
    prefix = _text(lang, line_key).split("{", 1)[0]
    tab.page.wait_for_function(LINE_STARTS_JS, arg=prefix, timeout=TIMEOUT_MS)
    tab.page.wait_for_function(CHECK_IDLE_JS, timeout=TIMEOUT_MS)


def _rows(tab: Tab) -> dict[str, str]:
    rows = tab.page.evaluate(ROWS_JS)
    assert all(not row["hidden"] for row in rows)
    return {row["id"]: row["text"] for row in rows}


def _clean(tab: Tab, browser_name: str) -> None:
    assert tab.csp_violations() == []
    assert unexpected_console(tab.console, browser_name, sys.platform) == []
    assert tab.errors == []
    assert tab.unexpected_failures() == []
    assert tab.blocked == []
    assert tab.foreign_requests() == []


# --- the pill and the dot at load ------------------------------------------------------------

STATE_OPTIONS = {
    "ok": ServerOptions(fake_provider=True),
    "warn": ServerOptions(fake_provider=True, ner_state="starting"),
    "danger": ServerOptions(),
}


@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize(
    ("overall", "server_options"), list(STATE_OPTIONS.items()), ids=list(STATE_OPTIONS)
)
def test_the_pill_and_the_dot_at_load_come_from_the_server(
    new_tab: NewTab, server: PanelServer, browser_name: str, overall: str, width: int
) -> None:
    tab = new_tab(width=width, locale="es-ES")
    _log_in(tab, server, "es")

    pill = tab.page.evaluate(VISIBLE_PILL_JS)
    assert pill["count"] == 2 and pill["shown"] == 1  # desktop header or phone bar, never both
    assert pill["label"] == _text("es", f"status.overall.{overall}")
    assert pill["text"].endswith(_text("es", "app_status.as_of"))
    assert pill["tones"] == [[overall], [overall]]
    dot = tab.page.evaluate(DOT_JS)
    if overall == "ok":
        assert dot["hidden"] is True and dot["shown"] is False
    else:
        assert dot["hidden"] is False and dot["shown"] is True
        assert dot["tones"] == [overall]
        key = "status.dot.danger" if overall == "danger" else "status.dot.warn"
        assert dot["text"] == _text("es", key)
        assert _text("es", key) in tab.page.evaluate(TAB_NAME_JS)
    assert _api_requests(tab) == []  # nothing asked at load
    _clean(tab, browser_name)


def test_nothing_is_asked_later_by_itself(tab: Tab, server: PanelServer) -> None:
    tab.page.clock.install()
    _log_in(tab, server, "en")

    tab.page.clock.run_for(27 * 60 * 1000)
    tab.page.evaluate(SETTLE_JS)

    assert _api_requests(tab) == []
    assert server.fake_provider_calls == 0


@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize("server_options", [STATE_OPTIONS["warn"]], ids=["warn"])
def test_a_failed_status_read_shows_not_refreshed_and_hides_the_dot(
    new_tab: NewTab, server: PanelServer, width: int
) -> None:
    tab = new_tab(width=width, locale="es-ES")
    _log_in(tab, server, "es")
    assert tab.page.evaluate(DOT_JS)["hidden"] is False
    tab.page.route(f"{tab.origin}{STATUS_PATH}", lambda route: route.abort())

    tab.page.click("#tab-status")
    tab.page.locator("#status-alert").wait_for(state="visible")

    pill = tab.page.evaluate(VISIBLE_PILL_JS)
    stale = _text("es", "status.overall.stale")
    assert pill["labels"] == [stale, stale]
    assert pill["tones"] == [["neutral"], ["neutral"]]
    assert tab.page.evaluate(DOT_JS)["hidden"] is True


@pytest.mark.parametrize("server_options", [STATE_OPTIONS["danger"]], ids=["danger"])
def test_a_status_answer_updates_the_pill_and_the_dot(new_tab: NewTab, server: PanelServer) -> None:
    tab = new_tab(locale="en-US")
    _log_in(tab, server, "en")
    assert tab.page.evaluate(DOT_JS)["tones"] == ["danger"]
    tab.page.route(
        f"{tab.origin}{STATUS_PATH}",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=_answer("starting", "warn")
        ),
    )

    _open_status(tab)

    pill = tab.page.evaluate(VISIBLE_PILL_JS)
    assert pill["labels"] == [_text("en", "status.overall.warn")] * 2
    assert pill["overall"] == ["warn", "warn"]
    dot = tab.page.evaluate(DOT_JS)
    assert dot["tones"] == ["warn"] and dot["hidden"] is False
    assert dot["text"] == _text("en", "status.dot.warn")


# --- «Comprobar ahora» -----------------------------------------------------------------------


@pytest.mark.parametrize("lang", ["es", "en"])
def test_check_now_asks_each_provider_once_and_says_the_result(
    new_tab: NewTab, server: PanelServer, browser_name: str, lang: str
) -> None:
    tab = new_tab(locale=_locale(lang))
    _log_in(tab, server, lang)
    _open_status(tab)
    assert server.fake_provider_calls == 0  # «Actualizar» and the first load are local only

    with tab.page.expect_response(_is_check):
        pressed = tab.page.evaluate(PRESS_CHECK_AND_READ_JS)
    prefix = _text(lang, "status.check.checked").split("{", 1)[0]
    tab.page.wait_for_function(LINE_STARTS_JS, arg=prefix, timeout=TIMEOUT_MS)
    tab.page.wait_for_function(CHECK_IDLE_JS, timeout=TIMEOUT_MS)

    assert pressed == {
        "check": "true",
        "refresh": "true",
        "disabled": False,
        "line": _text(lang, "status.check.checking"),
        "focused": True,
    }
    assert server.fake_provider_calls == 2
    assert server.fake_model_list_calls == 2
    assert server.provider_calls == []
    ok = _text(lang, "status.check.ok")
    assert _rows(tab) == {"openai": ok, "anthropic": ok}
    assert re.search(r"\d", tab.page.locator("#check-line").inner_text())
    assert tab.page.evaluate(ACTIVE_ID_JS) == "status-check"
    assert tab.page.locator("#status-refresh").get_attribute("aria-disabled") is None
    assert tab.page.evaluate(VISIBLE_PILL_JS)["overall"] == ["ok", "ok"]
    _clean(tab, browser_name)


CHECK_MODES = {
    "401": ("status.check.refused", "status.check.refused", "401"),
    "302": ("status.check.redirect", "status.check.redirect", None),
    "timeout": ("status.check.timeout", "status.check.timeout", None),
    "connect": ("status.check.unreachable", "status.check.unreachable", None),
    "mixed": ("status.check.ok", "status.check.refused", "401"),
}


@pytest.mark.parametrize(
    "server_options",
    [ServerOptions(fake_provider=True, check_mode=mode, check_seconds=0.5) for mode in CHECK_MODES],
    ids=list(CHECK_MODES),
)
def test_each_answer_of_a_provider_has_its_line_and_never_changes_the_pill(
    tab: Tab, server: PanelServer, browser_name: str
) -> None:
    openai_key, anthropic_key, code = CHECK_MODES[server.check_mode]
    _log_in(tab, server, "es")
    _open_status(tab)

    _press_check(tab, "es")

    def expected(key: str) -> str:
        return _text("es", key).replace("{code}", code or "")

    assert _rows(tab) == {"openai": expected(openai_key), "anthropic": expected(anthropic_key)}
    assert tab.page.evaluate(VISIBLE_PILL_JS)["overall"] == ["ok", "ok"]  # local: unchanged
    assert tab.page.evaluate(DOT_JS)["hidden"] is True
    assert server.fake_model_list_calls == 2  # a redirect is never followed
    assert server.provider_calls == []
    _clean(tab, browser_name)


@pytest.mark.parametrize("lang", ["es", "en"])
def test_a_second_press_within_the_cooldown_says_how_long_to_wait(
    new_tab: NewTab, server: PanelServer, lang: str
) -> None:
    tab = new_tab(locale=_locale(lang))
    _log_in(tab, server, lang)
    _open_status(tab)
    _press_check(tab, lang)

    _press_check(tab, lang, "status.check.cooldown")

    line = tab.page.locator("#check-line").inner_text()
    pattern = re.escape(_text(lang, "status.check.cooldown")).replace(r"\{s\}", r"(\d+)")
    found = re.fullmatch(pattern, line)
    assert found and 1 <= int(found.group(1)) <= 30
    assert server.fake_model_list_calls == 2  # the refused press asked nobody
    assert tab.page.evaluate(ACTIVE_ID_JS) == "status-check"


def test_a_busy_answer_says_a_check_is_running(tab: Tab, server: PanelServer) -> None:
    _log_in(tab, server, "es")
    _open_status(tab)
    tab.page.route(
        f"{tab.origin}{CHECK_PATH}",
        lambda route: route.fulfill(
            status=503,
            headers={"Retry-After": "1"},
            json={"error": {"code": "check_busy", "message": "x"}},
        ),
    )

    _press_check(tab, "es", "status.check.busy")

    assert tab.page.locator("#check-line").inner_text() == _text("es", "status.check.busy")


# --- review of 11e-4 --------------------------------------------------------------------------

REFUSALS = {
    429: ("check_cooldown", {"Retry-After": "12"}, "status.check.cooldown"),
    503: ("check_busy", {"Retry-After": "1"}, "status.check.busy"),
}
CALLOUT_JS = """() => ({shown: !document.getElementById('status-alert').hidden,
  text: document.getElementById('status-alert-text').textContent})"""


def _fail_a_refresh(tab: Tab) -> None:
    """«Estado» loaded, then a refresh the browser refuses: the callout and «Sin actualizar»."""
    _open_status(tab)
    tab.page.route(f"{tab.origin}{STATUS_PATH}", lambda route: route.abort())
    tab.page.click("#status-refresh")
    tab.page.locator("#status-alert").wait_for(state="visible")
    tab.page.wait_for_function(STATUS_IDLE_JS, timeout=TIMEOUT_MS)


@pytest.mark.parametrize("status", sorted(REFUSALS))
def test_a_refused_press_keeps_the_callout_of_a_failed_read(
    tab: Tab, server: PanelServer, status: int
) -> None:
    """Review 2: a 429 or 503 press says why on its line and leaves the error callout (and the
    stale pill) of the failed read as they were."""
    code, headers, line_key = REFUSALS[status]
    _log_in(tab, server, "es")
    _fail_a_refresh(tab)
    before = tab.page.evaluate(CALLOUT_JS)
    tab.page.route(
        f"{tab.origin}{CHECK_PATH}",
        lambda route: route.fulfill(
            status=status, headers=headers, json={"error": {"code": code, "message": "x"}}
        ),
    )

    _press_check(tab, "es", line_key)

    assert before["shown"] is True
    assert tab.page.evaluate(CALLOUT_JS) == before
    assert tab.page.evaluate(VISIBLE_PILL_JS)["overall"] == ["stale", "stale"]


FOREIGN_REFUSALS = {
    "429-no-code": (429, {"Retry-After": "12"}, "text/plain", "Too Many Requests"),
    "429-other-code": (
        429,
        {"Retry-After": "12"},
        "application/json",
        '{"error": {"code": "rate_limited", "message": "x"}}',
    ),
    "503-no-code": (503, {"Retry-After": "1"}, "text/html", "<h1>Service Unavailable</h1>"),
    "503-other-code": (
        503,
        {"Retry-After": "1"},
        "application/json",
        '{"error": {"code": "service_unavailable", "message": "x"}}',
    ),
}


@pytest.mark.parametrize("case", list(FOREIGN_REFUSALS))
def test_a_429_or_503_without_the_gateways_code_is_a_failed_check(
    tab: Tab, server: PanelServer, case: str
) -> None:
    """Review 4: busy and cooldown are decided by the body's error.code (check_busy,
    check_cooldown); the same status from something in front of the gateway is a failed check:
    the error callout, «Sin actualizar», and no «wait» or «running» line."""
    status, headers, content_type, body = FOREIGN_REFUSALS[case]
    _log_in(tab, server, "es")
    _open_status(tab)
    tab.page.route(
        f"{tab.origin}{CHECK_PATH}",
        lambda route: route.fulfill(
            status=status, headers=headers, content_type=content_type, body=body
        ),
    )

    with tab.page.expect_response(_is_check):
        tab.page.click("#status-check")
    tab.page.locator("#status-alert").wait_for(state="visible")
    tab.page.wait_for_function(CHECK_IDLE_JS, timeout=TIMEOUT_MS)

    assert tab.page.evaluate(CALLOUT_JS) == {
        "shown": True,
        "text": _text("es", "status.error.refused"),
    }
    assert tab.page.evaluate(VISIBLE_PILL_JS)["overall"] == ["stale", "stale"]
    assert tab.page.locator("#check-line").inner_text() == ""


VISIBILITY_JS = """(state) => {
  Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => state});
  Object.defineProperty(document, 'hidden', {configurable: true, get: () => state === 'hidden'});
  document.dispatchEvent(new Event('visibilitychange'));
}"""


def test_a_read_failing_during_a_check_never_hides_the_checks_fresh_snapshot(
    tab: Tab, server: PanelServer
) -> None:
    """Review 3: while a check runs, the page comes back to visible on «Estado». No status read
    starts then (the check brings a fresh snapshot); so a read that would fail cannot leave the
    pill «Sin actualizar» after the check answered."""
    held: list[Route] = []
    _log_in(tab, server, "en")
    _open_status(tab)
    reads_before = len([u for u in tab.requests if u.endswith(STATUS_PATH)])
    tab.page.route(f"{tab.origin}{CHECK_PATH}", lambda route: held.append(route))
    tab.page.locator("#status-check").dispatch_event("click")
    for _ in range(ROUND_TRIPS):
        if held:
            break
        tab.page.evaluate(SETTLE_JS)
    assert held, "the check never reached the route"
    tab.page.route(f"{tab.origin}{STATUS_PATH}", lambda route: route.abort())

    tab.page.evaluate(VISIBILITY_JS, "hidden")
    tab.page.evaluate(VISIBILITY_JS, "visible")
    for _ in range(5):  # a read started here would be aborted within a few tasks
        tab.page.evaluate(SETTLE_JS)
    with tab.page.expect_response(_is_check):
        held[0].continue_()
    prefix = _text("en", "status.check.checked").split("{", 1)[0]
    tab.page.wait_for_function(LINE_STARTS_JS, arg=prefix, timeout=TIMEOUT_MS)
    tab.page.wait_for_function(CHECK_IDLE_JS, timeout=TIMEOUT_MS)

    assert tab.page.evaluate(VISIBLE_PILL_JS)["overall"] == ["ok", "ok"]
    assert tab.page.evaluate(CALLOUT_JS)["shown"] is False
    assert len([u for u in tab.requests if u.endswith(STATUS_PATH)]) == reads_before


def test_a_401_goes_to_the_login_with_the_language(tab: Tab, server: PanelServer) -> None:
    _log_in(tab, server, "es")
    _open_status(tab)
    tab.page.route(
        f"{tab.origin}{CHECK_PATH}",
        lambda route: route.fulfill(status=401, json={"error": {"code": "panel_unauthorized"}}),
    )

    tab.page.click("#status-check")

    tab.page.wait_for_url(f"{server.origin}/panel/login?lang=es")


def test_a_press_while_checking_asks_once(tab: Tab, server: PanelServer) -> None:
    _log_in(tab, server, "en")
    _open_status(tab)

    with tab.page.expect_response(_is_check):
        tab.page.evaluate(
            "() => { const b = document.getElementById('status-check');"
            " b.click(); b.click(); b.click();"
            " document.getElementById('status-refresh').click(); }"
        )
    tab.page.wait_for_function(CHECK_IDLE_JS, timeout=TIMEOUT_MS)
    tab.page.evaluate(SETTLE_JS)

    assert len([u for u in tab.requests if u.endswith(CHECK_PATH)]) == 1
    assert len([u for u in tab.requests if u.endswith(STATUS_PATH)]) == 1  # refresh waited
    assert server.fake_model_list_calls == 2


def test_a_late_status_answer_never_overwrites_the_checks_newer_snapshot(
    tab: Tab, server: PanelServer
) -> None:
    """The first status load is held; a check answers (overall ok); then the held load answers
    with an older «danger» snapshot: it must not paint over the newer one."""
    held: list[Route] = []

    def hold(route: Route) -> None:
        held.append(route)

    _log_in(tab, server, "en")
    tab.page.route(f"{tab.origin}{STATUS_PATH}", hold)
    tab.page.click("#tab-status")
    for _ in range(ROUND_TRIPS):
        if held:
            break
        tab.page.evaluate(SETTLE_JS)
    assert held, "the first status request never reached the route"
    # The grid (and its button) shows only after a first answer: show it from a check.
    tab.page.evaluate("() => { document.getElementById('status-grid').hidden = false; }")
    with tab.page.expect_response(_is_check):
        tab.page.locator("#status-check").dispatch_event("click")
    tab.page.wait_for_function(CHECK_IDLE_JS, timeout=TIMEOUT_MS)
    assert tab.page.evaluate(VISIBLE_PILL_JS)["overall"] == ["ok", "ok"]

    with tab.page.expect_response(_is_status):
        held[0].fulfill(
            status=200, content_type="application/json", body=_answer("closed", "danger")
        )
    for _ in range(3):
        tab.page.evaluate(SETTLE_JS)

    assert tab.page.evaluate(VISIBLE_PILL_JS)["overall"] == ["ok", "ok"]
    assert tab.page.evaluate(DOT_JS)["hidden"] is True


# --- accessibility, colours and text size ----------------------------------------------------

MIXED = ServerOptions(fake_provider=True, check_mode="mixed")


@pytest.mark.parametrize("server_options", [MIXED], ids=["mixed"])
@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("lang", ["es", "en"])
def test_axe_finds_nothing_after_a_mixed_check(
    new_tab: NewTab, server: PanelServer, lang: str, theme: str, width: int
) -> None:
    tab = new_tab(bypass_csp=True, color_scheme=theme, width=width, locale=_locale(lang))
    _log_in(tab, server, lang)
    _open_status(tab)
    _press_check(tab, lang)

    report = tab.axe()

    assert report.passes > 0
    assert report.violations == "", f"incomplete rules {report.incomplete}"


@pytest.mark.parametrize("server_options", [MIXED], ids=["mixed"])
@pytest.mark.parametrize("width", [1280, 390])
def test_forced_colours_keep_the_word_and_the_dots(
    new_tab: NewTab, server: PanelServer, browser_name: str, width: int
) -> None:
    tab = new_tab(width=width, forced_colors="active", locale="es-ES")
    _log_in(tab, server, "es")
    _open_status(tab)
    _press_check(tab, "es")

    shown: dict[str, Any] = tab.page.evaluate(
        """() => { const p = [...document.querySelectorAll('[data-app-status]')]
          .find((x) => x.getClientRects().length > 0);
          const dot = p.querySelector('.app-status-dot');
          const line = document.querySelector('[data-check]');
          return {word: p.querySelector('[data-label]').textContent,
                  dot: getComputedStyle(dot).backgroundColor,
                  lineDot: getComputedStyle(line.querySelector('.dot')).backgroundColor}; }"""
    )
    assert shown["word"] == _text("es", "status.overall.ok")
    for colour in (shown["dot"], shown["lineDot"]):
        assert colour not in ("rgba(0, 0, 0, 0)", "transparent")
    assert tab.csp_violations() == []
    assert unexpected_console(tab.console, browser_name, sys.platform) == []


@pytest.mark.parametrize("server_options", [MIXED], ids=["mixed"])
@pytest.mark.parametrize("width", [1280, 390, 320])
def test_at_twice_the_text_size_the_checked_view_grows_and_nothing_clips(
    new_tab: NewTab, server: PanelServer, width: int
) -> None:
    tab = new_tab(width=width, bypass_csp=True, locale="es-ES")
    _log_in(tab, server, "es")
    _open_status(tab)
    _press_check(tab, "es")

    before = tab.page.evaluate(STATUS_SIZES_JS, True)
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
    assert tab.page.evaluate(VISIBLE_PILL_JS)["shown"] == 1
