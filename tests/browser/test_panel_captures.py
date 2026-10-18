"""Reference captures of the real panel (issue 53, PR 11c-2b): `make panel-captures` only.

Skipped unless --panel-captures. Writes 24 PNG files to build/panel-captures/ (git ignores
build/): for Spanish and English, the login page empty, the login page after a wrong token and
the panel, in light and dark, at 1280x800 and 390x844, both at 2x. With reduced motion,
animations disabled, the caret hidden and after every font is ready.

Synthetic only: the wrong token is a throwaway made here, the right one is the test gateway's
throwaway. Right before every capture the token field is checked to be empty (an error shows
nothing of what was typed: no length, no dots).
"""

import secrets
from pathlib import Path

import pytest

from tests.browser.harness import TIMEOUT_MS, NewTab, Tab
from tests.browser.server import PanelServer

pytestmark = pytest.mark.browser

CAPTURES = Path(__file__).resolve().parents[2] / "build" / "panel-captures"
SIZES = {1280: "1280x800", 390: "390x844"}
ALERT_SHOWN_JS = "() => document.getElementById('login-alert').textContent !== ''"


@pytest.fixture(autouse=True)
def _only_with_the_option(request: pytest.FixtureRequest) -> None:
    if not request.config.getoption("--panel-captures"):
        pytest.skip("reference captures: run `make panel-captures`")


def _capture(tab: Tab, name: str) -> Path:
    if tab.page.locator("#login-token").count():
        value = tab.page.input_value("#login-token")
        assert value == "", "the token field must be empty in every capture"
    tab.page.evaluate("() => document.fonts.ready.then(() => true)")
    path = CAPTURES / f"{name}.png"
    tab.page.screenshot(path=path, animations="disabled", caret="hide", timeout=TIMEOUT_MS)
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

    assert all(path.stat().st_size > 0 for path in written)
