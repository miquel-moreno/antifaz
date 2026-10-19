"""Reference captures of the real panel (issue 53, PR 11c-2b): `make panel-captures` only.

Skipped unless --panel-captures. Writes 56 PNG files to build/panel-captures/ (git ignores
build/): for Spanish and English, the login page empty, the login page after a wrong token and
the panel, in light and dark, at 1280x800 and 390x844, both at 2x; at 390x844 the panel with
the settings menu open and its theme switch (`*-390x844-4-panel-menu.png`); and at 390x844 and
320x844 the panel with the text at 200 % (11c-3a): where the names do not fit, the bottom bar
scrolls and fades on the side that has more, on the first tab (`*-5-text200-first.png`) and on
«Estado», the last (`*-6-text200-status.png`). 11c-3b: the «Estado» view with its data (both
providers with a key on their official addresses, the NER working), the whole page on the
desktop and the window on a phone (its tab bar is fixed), for both
languages, themes and sizes (`*-7-status.png`), and its error state, Spanish light at both sizes
(`es-light-*-8-status-error.png`: the request refused by the browser, the last data kept), and
every warning at once, Spanish light at both sizes (`es-light-*-9-status-warnings.png`: the NER
stopped, an address with a user part, a provider without a key; a fixed answer). With
reduced motion, animations disabled, the caret hidden and after every font is ready.

Synthetic only: the wrong token is a throwaway made here, the right one is the test gateway's
throwaway. Right before every capture the token field is checked to be empty (an error shows
nothing of what was typed: no length, no dots).
"""

import secrets
from pathlib import Path

import pytest

from tests.browser.harness import TIMEOUT_MS, NewTab, Tab
from tests.browser.server import PanelServer, ServerOptions
from tests.browser.test_panel_status import fulfill_warnings

pytestmark = pytest.mark.browser

CAPTURES = Path(__file__).resolve().parents[2] / "build" / "panel-captures"
SIZES = {1280: "1280x800", 390: "390x844"}
ALERT_SHOWN_JS = "() => document.getElementById('login-alert').textContent !== ''"
# The fade classes agree with where the tab bar really is scrolled (panel.js sets them).
FADE_SETTLED_JS = """() => {
  const l = document.querySelector('[role=tablist]');
  const max = l.scrollWidth - l.clientWidth;
  return l.classList.contains('is-more-start') === (max > 1 && l.scrollLeft > 1)
    && l.classList.contains('is-more-end') === (max > 1 && l.scrollLeft < max - 1);
}"""


# Idle and filled in: the button enabled again and the line says when it was checked.
STATUS_IDLE_JS = """() =>
  document.getElementById('status-refresh').getAttribute('aria-disabled') !== 'true' &&
  document.getElementById('status-time').textContent !== ''"""


@pytest.fixture
def server_options() -> ServerOptions:
    """Both providers with a throwaway key (never called) and a NER that says "ok"."""
    return ServerOptions(provider_keys=True, ner_state="ok")


@pytest.fixture(autouse=True)
def _only_with_the_option(request: pytest.FixtureRequest) -> None:
    if not request.config.getoption("--panel-captures"):
        pytest.skip("reference captures: run `make panel-captures`")


def _capture(tab: Tab, name: str, *, full_page: bool = False) -> Path:
    if tab.page.locator("#login-token").count():
        value = tab.page.input_value("#login-token")
        assert value == "", "the token field must be empty in every capture"
    tab.page.evaluate("() => document.fonts.ready.then(() => true)")
    path = CAPTURES / f"{name}.png"
    tab.page.screenshot(
        path=path, animations="disabled", caret="hide", timeout=TIMEOUT_MS, full_page=full_page
    )
    return path


@pytest.mark.parametrize("width", sorted(SIZES, reverse=True))
@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("lang", ["es", "en"])
def test_capture_the_panel(
    new_tab: NewTab, server: PanelServer, lang: str, theme: str, width: int
) -> None:
    CAPTURES.mkdir(parents=True, exist_ok=True)
    stem = f"{lang}-{theme}-{SIZES[width]}"
    tab = new_tab(color_scheme=theme, width=width, scale=2)
    tab.goto(f"/panel/login?lang={lang}")
    written = [_capture(tab, f"{stem}-1-login")]

    tab.page.fill("#login-token", secrets.token_hex(32))
    tab.page.click("#login-form button[type=submit]")
    tab.page.wait_for_function(ALERT_SHOWN_JS, timeout=TIMEOUT_MS)
    tab.page.locator("#login-token").blur()  # no focus ring in the reference
    written.append(_capture(tab, f"{stem}-2-login-error"))

    tab.page.fill("#login-token", server.admin_token)
    tab.page.click("#login-form button[type=submit]")
    tab.page.wait_for_url(f"{server.origin}/panel?lang={lang}")
    tab.page.evaluate("() => document.fonts.ready.then(() => true)")
    written.append(_capture(tab, f"{stem}-3-panel"))
    if width < 768:  # the phone's settings menu, open
        tab.page.click("#settings-btn")
        tab.page.locator("#settings-menu").wait_for(state="visible")
        written.append(_capture(tab, f"{stem}-4-panel-menu"))

    assert all(path.stat().st_size > 0 for path in written)


@pytest.mark.parametrize("width", [390, 320])
@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("lang", ["es", "en"])
def test_capture_the_phone_at_twice_the_text_size(
    new_tab: NewTab, server: PanelServer, lang: str, theme: str, width: int
) -> None:
    """The text at 200 % (a style tag, so the CSP is bypassed in this context only). Where the
    five names fit (English at 390 px) the bar does not scroll and shows no fade."""
    CAPTURES.mkdir(parents=True, exist_ok=True)
    stem = f"{lang}-{theme}-{width}x844"
    tab = new_tab(color_scheme=theme, width=width, scale=2, bypass_csp=True)
    tab.goto(f"/panel/login?lang={lang}")
    tab.page.fill("#login-token", server.admin_token)
    tab.page.click("#login-form button[type=submit]")
    tab.page.wait_for_url(f"{server.origin}/panel?lang={lang}")
    tab.page.add_style_tag(content="html{font-size:200%}")
    tab.page.wait_for_function(FADE_SETTLED_JS, timeout=TIMEOUT_MS)
    written = [_capture(tab, f"{stem}-5-text200-first")]
    tab.page.click("#tab-status")
    tab.page.locator("#tab-status").blur()  # no focus ring in the reference
    tab.page.wait_for_function(FADE_SETTLED_JS, timeout=TIMEOUT_MS)
    written.append(_capture(tab, f"{stem}-6-text200-status"))

    assert all(path.stat().st_size > 0 for path in written)


def _log_in(tab: Tab, server: PanelServer, lang: str) -> None:
    tab.goto(f"/panel/login?lang={lang}")
    tab.page.fill("#login-token", server.admin_token)
    tab.page.click("#login-form button[type=submit]")
    tab.page.wait_for_url(f"{server.origin}/panel?lang={lang}")
    tab.page.evaluate("() => document.fonts.ready.then(() => true)")


def _open_status(tab: Tab) -> None:
    tab.page.click("#tab-status")
    tab.page.wait_for_function(STATUS_IDLE_JS, timeout=TIMEOUT_MS)
    tab.page.locator("#tab-status").blur()  # no focus ring in the reference


@pytest.mark.parametrize("width", sorted(SIZES, reverse=True))
@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("lang", ["es", "en"])
def test_capture_the_status_view(
    new_tab: NewTab, server: PanelServer, lang: str, theme: str, width: int
) -> None:
    CAPTURES.mkdir(parents=True, exist_ok=True)
    tab = new_tab(color_scheme=theme, width=width, scale=2)
    _log_in(tab, server, lang)
    _open_status(tab)

    # Whole page on the desktop; the phone's tab bar is fixed, so there the window only.
    path = _capture(tab, f"{lang}-{theme}-{SIZES[width]}-7-status", full_page=width >= 768)

    assert path.stat().st_size > 0


@pytest.mark.parametrize("width", sorted(SIZES, reverse=True))
def test_capture_the_status_error(new_tab: NewTab, server: PanelServer, width: int) -> None:
    CAPTURES.mkdir(parents=True, exist_ok=True)
    tab = new_tab(color_scheme="light", width=width, scale=2)
    _log_in(tab, server, "es")
    _open_status(tab)
    tab.page.route(f"{tab.origin}/panel/api/status", lambda route: route.abort())
    tab.page.click("#status-refresh")
    tab.page.locator("#status-alert").wait_for(state="visible")
    tab.page.wait_for_function(STATUS_IDLE_JS, timeout=TIMEOUT_MS)
    tab.page.locator("#status-refresh").blur()
    tab.page.mouse.move(0, 0)  # no hover on the button in the reference

    path = _capture(tab, f"es-light-{SIZES[width]}-8-status-error", full_page=width >= 768)

    assert path.stat().st_size > 0


@pytest.mark.parametrize("width", sorted(SIZES, reverse=True))
def test_capture_the_status_warnings(new_tab: NewTab, server: PanelServer, width: int) -> None:
    CAPTURES.mkdir(parents=True, exist_ok=True)
    tab = new_tab(color_scheme="light", width=width, scale=2)
    _log_in(tab, server, "es")
    tab.page.route(f"{tab.origin}/panel/api/status", fulfill_warnings)
    _open_status(tab)
    if width < 768:  # the window only (fixed tab bar): the providers card with its warning
        tab.page.evaluate("() => document.getElementById('s-prov').scrollIntoView()")
        tab.page.evaluate("() => document.fonts.ready.then(() => true)")

    path = _capture(tab, f"es-light-{SIZES[width]}-9-status-warnings", full_page=width >= 768)

    assert path.stat().st_size > 0
