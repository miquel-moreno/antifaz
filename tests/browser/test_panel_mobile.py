"""The panel on a phone (issue 53, review of the 11c-2b captures): Chromium, Firefox, WebKit.

- The five tabs are a bar fixed at the bottom of the screen, within thumb reach: icon and a short
  name each, none cut at 360, 390 or 430 px, the same ARIA tabs (horizontal arrows, Home/End,
  keys 1-5 scoped as on the desktop), and the content never hides behind the bar.
- The top bar holds only the logo and a settings button. The button opens a menu (disclosure:
  aria-expanded, aria-controls) with the language, the theme and «Salir»: the focus moves into
  it, Escape closes it and gives the focus back to the button, a click outside closes it, and
  nothing is stored.
- The login and the panel fill the screen: no floating card with empty space below.
- On the desktop nothing of this shows: sidebar with «Salir», language and theme top right.

No token, cookie value or page HTML inside an assert (tests/unit/test_browser_harness_rules.py).
"""

import re

import pytest

from antifaz.web.i18n import load_catalogs
from tests.browser.harness import NewTab, Tab
from tests.browser.server import PanelServer

pytestmark = pytest.mark.browser

CATALOGS = load_catalogs()
TABS = ("playground", "live", "keys", "policies", "status")
PHONES = [320, 360, 390, 430]
STORAGE_JS = """() => ({cookieVisible: document.cookie !== '', local: localStorage.length,
                     session: sessionStorage.length})"""
NOTHING_STORED = {"cookieVisible": False, "local": 0, "session": 0}
BOX_JS = """(selector) => {
  const r = document.querySelector(selector).getBoundingClientRect();
  return {top: r.top, bottom: r.bottom, left: r.left, right: r.right, width: r.width,
          height: r.height, vw: document.documentElement.clientWidth, vh: window.innerHeight};
}"""
# Every tab's short name: shown, whole (no overflow) and inside its tab; the long one hidden.
LABELS_JS = """() => [...document.querySelectorAll('[role=tab]')].map((tab) => {
  const short = tab.querySelector('.app-tab-short');
  const long = tab.querySelector('.app-tab-label');
  const t = tab.getBoundingClientRect(), s = short.getBoundingClientRect();
  return {id: tab.id, text: short.textContent, shown: s.width > 0 && s.height > 0,
          whole: short.scrollWidth <= short.clientWidth + 1,
          inside: s.left >= t.left - 0.5 && s.right <= t.right + 0.5,
          longShown: long.getBoundingClientRect().width > 0,
          tabWhole: tab.scrollWidth <= tab.clientWidth + 1};
})"""
ACTIVE_IN_MENU_JS = "() => !!document.activeElement.closest('#settings-menu')"
ACTIVE_ID_JS = "() => document.activeElement ? document.activeElement.id : null"
THEME_JS = "() => document.documentElement.getAttribute('data-theme')"
SELECTED_JS = """() => [...document.querySelectorAll('[role=tab]')]
  .filter((t) => t.getAttribute('aria-selected') === 'true').map((t) => t.id)"""


def _log_in(tab: Tab, server: PanelServer, lang: str = "en") -> None:
    tab.goto(f"/panel/login?lang={lang}")
    tab.page.fill("#login-token", server.admin_token)
    tab.page.click("#login-form button[type=submit]")
    tab.page.wait_for_url(f"{server.origin}/panel?lang={lang}")
    tab.page.evaluate("() => document.fonts.ready.then(() => true)")
    tab.remember_secrets()


def _box(tab: Tab, selector: str) -> dict[str, float]:
    box: dict[str, float] = tab.page.evaluate(BOX_JS, selector)
    return box


def _open_menu(tab: Tab) -> None:
    tab.page.click("#settings-btn")
    assert tab.page.get_attribute("#settings-btn", "aria-expanded") == "true"
    assert tab.page.locator("#settings-menu").is_visible()


# --- the bottom bar -------------------------------------------------------------------------------


@pytest.mark.parametrize("width", PHONES)
def test_the_tabs_are_a_bar_fixed_at_the_bottom(
    new_tab: NewTab, server: PanelServer, width: int
) -> None:
    tab = new_tab(width=width, locale="es-ES")
    _log_in(tab, server, "es")

    bar = _box(tab, "[role=tablist]")
    assert bar["bottom"] == pytest.approx(bar["vh"], abs=1)
    assert bar["left"] == pytest.approx(0, abs=1)
    assert bar["right"] == pytest.approx(bar["vw"], abs=1)
    assert tab.page.get_attribute("[role=tablist]", "aria-orientation") == "horizontal"
    labels = tab.page.evaluate(LABELS_JS)
    assert [label["id"] for label in labels] == [f"tab-{name}" for name in TABS]
    for label, name in zip(labels, TABS, strict=True):
        assert label["text"] == CATALOGS["es"].text(f"tab.{name}.short")
        assert label["shown"] and label["whole"] and label["inside"] and label["tabWhole"], label
        assert not label["longShown"], label
    no_overflow = "() => document.documentElement.scrollWidth <= window.innerWidth"
    assert tab.page.evaluate(no_overflow)


@pytest.mark.parametrize(("width", "height"), [(320, 480), (390, 500)])
def test_the_bar_stays_at_the_bottom_when_the_page_scrolls(
    new_tab: NewTab, server: PanelServer, width: int, height: int
) -> None:
    """A short screen, so the page really scrolls: the bar stays at the bottom and the end of the
    view stops above it."""
    tab = new_tab(width=width, height=height)
    _log_in(tab, server)
    tab.page.evaluate("() => window.scrollTo(0, document.documentElement.scrollHeight)")
    assert tab.page.evaluate("() => window.scrollY") > 0
    bar = _box(tab, "[role=tablist]")
    assert bar["bottom"] == pytest.approx(bar["vh"], abs=1)
    last = _box(tab, "#panel-playground > :last-child")
    assert last["bottom"] <= bar["top"] + 0.5


# A stand-in for a classic Windows scrollbar (17 px), so the window is narrower than the viewport
# by the same amount (the headless browsers draw overlay scrollbars).
SCROLLBAR_CSS = "body{margin-right:17px}"


@pytest.mark.parametrize("width", [640, 660, 680, 688, 700])
def test_one_width_decides_both_the_phone_bar_and_the_window_look(
    new_tab: NewTab, server: PanelServer, width: int
) -> None:
    """Review of 11c-2b: at 673-688 px with a classic scrollbar the window kept its card look
    (and its backdrop filter, which held the fixed bar) while the phone rules already applied.
    Whatever layout a width gets, it must be whole: the phone bar at the bottom of the screen,
    or the desktop sidebar inside the card."""
    tab = new_tab(width=width, bypass_csp=True)
    tab.goto("/panel/login")
    tab.page.add_style_tag(content=SCROLLBAR_CSS)
    tab.page.fill("#login-token", server.admin_token)
    tab.page.click("#login-form button[type=submit]")
    tab.page.wait_for_url(f"{server.origin}/panel")
    tab.page.add_style_tag(content=SCROLLBAR_CSS)
    orientation = tab.page.get_attribute("[role=tablist]", "aria-orientation")
    bar = _box(tab, "[role=tablist]")
    if orientation == "horizontal":  # the phone layout
        assert bar["bottom"] == pytest.approx(bar["vh"], abs=1)
        assert bar["left"] == pytest.approx(0, abs=1)
        assert bar["right"] == pytest.approx(bar["vw"], abs=1)
        for label in tab.page.evaluate(LABELS_JS):
            assert label["shown"] and label["whole"] and label["inside"], label
    else:  # the desktop layout: the bar is in the sidebar
        side = _box(tab, ".app-sidebar")
        assert bar["left"] >= side["left"] and bar["right"] <= side["right"]
    if width == 640:
        assert orientation == "horizontal"
    if width == 700:
        assert orientation == "vertical"


def test_a_focused_element_is_never_hidden_under_the_bars(
    new_tab: NewTab, server: PanelServer
) -> None:
    """WCAG 2.4.11 (focus not obscured): scroll-padding keeps a focused element clear of the
    bottom tab bar and of the sticky top bar. A test-only button at the end of the view is
    reached with Tab on a short screen."""
    tab = new_tab(width=390, height=500)
    _log_in(tab, server)
    tab.page.evaluate(
        "() => { const b = document.createElement('button'); b.id = 'probe-end';"
        " b.className = 'btn'; b.textContent = 'x';"
        " document.querySelector('#panel-playground').appendChild(b); }"
    )
    tab.page.focus("#panel-playground")
    tab.page.keyboard.press("Tab")
    assert tab.page.evaluate(ACTIVE_ID_JS) == "probe-end"
    # WebKit may scroll the focused element into view on the next frame: let two frames pass.
    tab.page.evaluate(
        "() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))"
    )
    probe = _box(tab, "#probe-end")
    bar = _box(tab, "[role=tablist]")
    top_bar = _box(tab, ".app-sidebar")
    assert probe["bottom"] <= bar["top"] + 0.5
    assert probe["top"] >= top_bar["bottom"] - 0.5


def test_the_short_names_survive_twice_the_text_size(new_tab: NewTab, server: PanelServer) -> None:
    """Text resized to 200 % (WCAG 1.4.4): the short names wrap instead of spilling out."""
    tab = new_tab(width=390, bypass_csp=True)
    _log_in(tab, server, "es")
    tab.page.add_style_tag(content="html{font-size:200%}")
    for label in tab.page.evaluate(LABELS_JS):
        assert label["shown"] and label["whole"] and label["inside"] and label["tabWhole"], label


@pytest.mark.parametrize("lang", ["es", "en"])
def test_no_short_name_is_cut_at_the_narrowest_phone(
    new_tab: NewTab, server: PanelServer, lang: str
) -> None:
    tab = new_tab(width=320)
    _log_in(tab, server, lang)
    for label in tab.page.evaluate(LABELS_JS):
        assert label["shown"] and label["whole"] and label["inside"] and label["tabWhole"], label


def test_the_bottom_bar_keeps_the_tabs_keyboard(new_tab: NewTab, server: PanelServer) -> None:
    tab = new_tab(width=390)
    _log_in(tab, server)
    tab.page.focus("#tab-playground")
    for key, expected in [
        ("ArrowRight", "tab-live"),
        ("ArrowDown", "tab-live"),  # horizontal: up and down do nothing
        ("End", "tab-status"),
        ("ArrowRight", "tab-playground"),
        ("Home", "tab-playground"),
        ("4", "tab-policies"),  # in the tab list, 1-5 work
    ]:
        tab.page.keyboard.press(key)
        assert tab.page.evaluate(SELECTED_JS) == [expected], key
        assert tab.page.evaluate(ACTIVE_ID_JS) == expected
    tab.page.focus("#settings-btn")
    tab.page.keyboard.press("2")  # outside the tabs and the views: nothing
    assert tab.page.evaluate(SELECTED_JS) == ["tab-policies"]


# --- the top bar and its settings menu ------------------------------------------------------------


def test_the_top_bar_holds_only_the_logo_and_the_settings_button(
    new_tab: NewTab, server: PanelServer
) -> None:
    tab = new_tab(width=390, locale="es-ES")
    _log_in(tab, server, "es")

    assert tab.page.locator(".app-brand .app-logo").is_visible()
    button = tab.page.locator("#settings-btn")
    assert button.is_visible()
    assert button.get_attribute("aria-label") == CATALOGS["es"].text("settings.label")
    assert button.get_attribute("aria-expanded") == "false"
    assert button.get_attribute("aria-controls") == "settings-menu"
    assert not tab.page.locator("#settings-menu").is_visible()
    top_bar = _box(tab, ".app-sidebar")
    assert top_bar["height"] <= 80  # one row: no empty band under the logo
    size = _box(tab, "#settings-btn")
    assert size["width"] >= 40 and size["height"] >= 40  # a comfortable touch target
    for hidden in (
        ".app-header",
        ".app-sidebar-foot",
        "a[hreflang]",
        ".theme-btn",
        "[data-logout]",
    ):
        assert tab.page.locator(f"{hidden}:visible").count() == 0, hidden


def test_the_settings_menu_opens_closes_and_gives_the_focus_back(
    new_tab: NewTab, server: PanelServer
) -> None:
    tab = new_tab(width=390)
    _log_in(tab, server)

    _open_menu(tab)
    assert tab.page.evaluate(ACTIVE_IN_MENU_JS)  # the focus moved into the menu
    for item in ("a[hreflang=es]", ".theme-btn", "[data-logout]"):
        assert tab.page.locator(f"#settings-menu {item}").is_visible(), item
    tab.page.keyboard.press("Tab")
    assert tab.page.evaluate(ACTIVE_IN_MENU_JS)  # Tab goes on inside the menu
    tab.page.keyboard.press("Escape")
    assert not tab.page.locator("#settings-menu").is_visible()
    assert tab.page.get_attribute("#settings-btn", "aria-expanded") == "false"
    assert tab.page.evaluate(ACTIVE_ID_JS) == "settings-btn"

    _open_menu(tab)
    tab.page.keyboard.press("Shift+Tab")  # back to the button: the menu stays
    assert tab.page.evaluate(ACTIVE_ID_JS) == "settings-btn"
    assert tab.page.locator("#settings-menu").is_visible()
    tab.page.mouse.click(195, 600)  # a click outside (on the view) closes it
    assert not tab.page.locator("#settings-menu").is_visible()
    assert tab.page.get_attribute("#settings-btn", "aria-expanded") == "false"
    assert tab.page.evaluate(STORAGE_JS) == NOTHING_STORED


def test_the_menu_theme_row_says_its_name_and_the_name_is_the_text(
    new_tab: NewTab, server: PanelServer
) -> None:
    """WCAG 2.5.3 (label in name): in the menu the theme row shows a word from the catalog, and
    that word is its accessible name (no aria-label of its own)."""
    tab = new_tab(width=390, locale="es-ES")
    _log_in(tab, server, "es")
    _open_menu(tab)
    row = tab.page.locator("#settings-menu .theme-btn")
    assert row.get_attribute("aria-label") is None
    icon = _box(tab, "#settings-menu .theme-btn .ic")
    text = _box(tab, "#settings-menu .theme-text")
    assert icon["right"] <= text["left"]  # icon and word side by side, on one row
    assert text["top"] < (icon["top"] + icon["bottom"]) / 2 < text["bottom"]
    assert row.inner_text().strip() == CATALOGS["es"].text("theme.dark")  # light scheme
    desktop_button = tab.page.locator(".app-header .theme-btn")  # icon only, named by aria-label
    assert desktop_button.get_attribute("aria-label") == CATALOGS["es"].text("theme.to_dark")
    row.click()
    assert row.inner_text().strip() == CATALOGS["es"].text("theme.light")


def test_the_menu_closes_when_the_phone_layout_goes(new_tab: NewTab, server: PanelServer) -> None:
    tab = new_tab(width=390)
    _log_in(tab, server)
    _open_menu(tab)
    tab.page.set_viewport_size({"width": 1280, "height": 800})
    tab.page.wait_for_function(
        "() => document.getElementById('settings-btn').getAttribute('aria-expanded') === 'false'"
    )
    assert not tab.page.locator("#settings-menu").is_visible()
    tab.page.focus("#tab-live")
    tab.page.keyboard.press("Escape")  # no hidden button takes the focus
    assert tab.page.evaluate(ACTIVE_ID_JS) == "tab-live"


def test_leaving_the_button_backwards_closes_the_menu(new_tab: NewTab, server: PanelServer) -> None:
    tab = new_tab(width=390)
    _log_in(tab, server)
    # A focusable element just before the button: WebKit's Tab skips links, so without it the
    # focus would leave the page (no relatedTarget), which deliberately keeps the menu open
    # (Safari does not focus a clicked button, and a click in the menu must not close it).
    tab.page.evaluate(
        "() => { const b = document.createElement('button'); b.id = 'probe-before';"
        " b.textContent = 'x'; const s = document.getElementById('settings-btn');"
        " s.parentNode.insertBefore(b, s); }"
    )
    _open_menu(tab)
    tab.page.keyboard.press("Shift+Tab")  # to the button: still open
    assert tab.page.evaluate(ACTIVE_ID_JS) == "settings-btn"
    assert tab.page.locator("#settings-menu").is_visible()
    tab.page.keyboard.press("Shift+Tab")  # out of the button and the menu: closed
    assert tab.page.evaluate(ACTIVE_ID_JS) == "probe-before"
    assert not tab.page.locator("#settings-menu").is_visible()
    assert tab.page.get_attribute("#settings-btn", "aria-expanded") == "false"


def test_the_settings_menu_changes_theme_and_language_and_logs_out(
    new_tab: NewTab, server: PanelServer
) -> None:
    tab = new_tab(width=390)
    _log_in(tab, server)

    _open_menu(tab)
    tab.page.click("#settings-menu .theme-btn")
    assert tab.page.evaluate(THEME_JS) == "dark"
    assert tab.page.locator("#settings-menu").is_visible()  # it stays open for another change
    tab.page.click("#settings-menu a[hreflang=es]")
    tab.page.wait_for_url(f"{server.origin}/panel?lang=es")
    assert tab.page.evaluate(THEME_JS) is None  # nothing was kept
    assert tab.page.evaluate(STORAGE_JS) == NOTHING_STORED

    _open_menu(tab)
    logout = tab.page.locator("#settings-menu [data-logout]")
    assert logout.inner_text().strip() == CATALOGS["es"].text("logout")
    logout.click()
    tab.page.wait_for_url(f"{server.origin}/panel/login?lang=es")
    assert tab.cookie_names() == []


# --- full height ----------------------------------------------------------------------------------


@pytest.mark.parametrize("width", PHONES)
def test_the_login_and_the_panel_fill_the_screen(
    new_tab: NewTab, server: PanelServer, width: int
) -> None:
    tab = new_tab(width=width)
    tab.goto("/panel/login")
    card = _box(tab, ".login-card")
    assert card["left"] == pytest.approx(0, abs=1)
    assert card["right"] == pytest.approx(card["vw"], abs=1)
    assert card["bottom"] >= card["vh"] - 1  # no empty space below

    _log_in(tab, server)
    window = _box(tab, ".app-window")
    assert window["left"] == pytest.approx(0, abs=1)
    assert window["top"] == pytest.approx(0, abs=1)
    assert window["right"] == pytest.approx(window["vw"], abs=1)
    assert window["bottom"] >= window["vh"] - 1
    radius = tab.page.evaluate(
        "() => getComputedStyle(document.querySelector('.app-window')).borderTopLeftRadius"
    )
    assert re.fullmatch(r"0(px)?", radius)


# --- axe with the menu open -----------------------------------------------------------------------


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("locale", ["es-ES", "en-US"])
def test_axe_finds_no_violation_with_the_menu_open(
    new_tab: NewTab, server: PanelServer, theme: str, locale: str
) -> None:
    tab = new_tab(bypass_csp=True, color_scheme=theme, width=390, locale=locale)
    lang = locale[:2]
    _log_in(tab, server, lang)
    _open_menu(tab)
    report = tab.axe()
    assert report.passes > 0
    assert report.violations == "", f"incomplete rules {report.incomplete}"


# --- the desktop does not change ------------------------------------------------------------------


def test_the_desktop_keeps_its_layout(new_tab: NewTab, server: PanelServer) -> None:
    tab = new_tab(width=1280)
    _log_in(tab, server)

    assert not tab.page.locator("#settings-btn").is_visible()
    assert tab.page.locator(".app-header a[hreflang=es]").is_visible()
    assert tab.page.locator(".app-header .theme-btn").is_visible()
    assert tab.page.locator(".app-sidebar-foot [data-logout]").is_visible()
    assert tab.page.get_attribute("[role=tablist]", "aria-orientation") == "vertical"
    bar = _box(tab, "[role=tablist]")
    side = _box(tab, ".app-sidebar")
    assert bar["left"] >= side["left"] and bar["right"] <= side["right"]
    for name in TABS:
        long_label = tab.page.locator(f"#tab-{name} .app-tab-label")
        assert long_label.is_visible()
        assert long_label.inner_text() == CATALOGS["en"].text(f"tab.{name}")
        assert not tab.page.locator(f"#tab-{name} .app-tab-short").is_visible()
