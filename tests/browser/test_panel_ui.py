"""The panel's interface in real browsers (issue 53, PR 11c-2b, ADR-0018): Chromium, Firefox,
WebKit, on the harness of 11c-2a (one gateway and one context per test, nothing but 127.0.0.1).

- The login through the real form: right token -> the panel; wrong, empty or too many -> the
  catalog's message in the page's language, the field empty and focused, nothing stored, and the
  token nowhere in the DOM, the URLs or the console.
- The language: the browser's locale (es, en, fr -> en) and the selector (plain links, nothing
  stored), kept from the login to the panel and back.
- The tabs with the keyboard (vertical in the sidebar, horizontal on a phone), «Salir» on the
  desktop and on a phone, and the back button afterwards.
- No CSP violation and a clean console after clicking everything, in both languages; the theme
  button stores nothing.
- Cross-site and same-site: a page of http://localhost:<port> (another site than 127.0.0.1)
  and one of http://127.0.0.1:<other port> (the same site, so the session cookie travels) post
  to the login and to the logout of the gateway on 127.0.0.1: the door refuses all of it.

No token, cookie value or page HTML inside an assert (tests/unit/test_browser_harness_rules.py).
"""

import re
import secrets
import sys

import pytest

from antifaz.web.headers import CONTENT_SECURITY_POLICY
from antifaz.web.i18n import load_catalogs
from tests.browser.console_rules import unexpected_console
from tests.browser.harness import TIMEOUT_MS, NewTab, Tab
from tests.browser.server import OtherSite, PanelServer

pytestmark = pytest.mark.browser

CATALOGS = load_catalogs()
SESSION_COOKIE = "antifaz_session"
TABS = ("playground", "live", "keys", "policies", "status")
STORAGE_JS = """() => ({cookieVisible: document.cookie !== '', local: localStorage.length,
                     session: sessionStorage.length})"""
NOTHING_STORED = {"cookieVisible": False, "local": 0, "session": 0}
INDEXED_DB_JS = "() => indexedDB.databases ? indexedDB.databases().then((d) => d.length) : 0"
ALERT_SHOWN_JS = "() => document.getElementById('login-alert').textContent !== ''"
SELECTED_JS = """() => [...document.querySelectorAll('[role=tab]')].map((t) =>
  [t.id, t.getAttribute('aria-selected'), t.tabIndex,
   document.getElementById(t.getAttribute('aria-controls')).hidden])"""
ACTIVE_ID_JS = "() => document.activeElement ? document.activeElement.id : null"
LANG_JS = "() => document.documentElement.lang"
THEME_JS = "() => document.documentElement.getAttribute('data-theme')"
# What another site can try without the user's help: a JSON fetch (needs a CORS preflight), a
# no-cors text/plain fetch (no preflight) and a text/plain form whose body is valid JSON.
XSITE_JSON_FETCH = """async ([url, body]) => {
  try {
    const r = await fetch(url, {method: 'POST', credentials: 'include',
      headers: {'Content-Type': 'application/json'}, body});
    return 'answered ' + r.status;
  } catch (e) { return 'blocked'; }
}"""
XSITE_PLAIN_FETCH = """async ([url, body]) => {
  try {
    const r = await fetch(url, {method: 'POST', mode: 'no-cors', credentials: 'include',
      headers: {'Content-Type': 'text/plain'}, body});
    return r.type;
  } catch (e) { return 'blocked'; }
}"""
XSITE_FORM = """([url, name, value]) => {
  const form = document.createElement('form');
  form.method = 'post'; form.action = url; form.enctype = 'text/plain';
  const input = document.createElement('input');
  input.name = name; input.value = value; form.appendChild(input);
  document.body.appendChild(form); form.submit();
}"""


def _clean(tab: Tab, browser_name: str, refused: int = 0, since: int = 0) -> None:
    """No CSP violation, nothing in the console (from line `since` on) but the `refused` login
    refusals the test caused on purpose, coming from /panel/login (console_rules.py), no failed
    or foreign request."""
    assert tab.csp_violations() == []
    lines = tab.console[since:]
    where = tab.console_locations[since:]
    assert unexpected_console(lines, browser_name, sys.platform, where, refused=refused) == []
    assert tab.errors == []
    assert tab.failed == []
    assert tab.blocked == []
    assert tab.foreign_requests() == []


def _goto(tab: Tab, path: str) -> None:
    tab.goto(path)
    assert tab.csp_header() == CONTENT_SECURITY_POLICY


def _submit(tab: Tab, token: str) -> None:
    tab.page.fill("#login-token", token)
    tab.page.click("#login-form button[type=submit]")


def _log_in_with_the_form(tab: Tab, server: PanelServer) -> None:
    _submit(tab, server.admin_token)
    tab.page.wait_for_url(re.compile(re.escape(server.origin) + r"/panel(\?lang=[a-z]{2})?$"))
    tab.page.evaluate("() => document.fonts.ready.then(() => true)")
    tab.remember_secrets()


def _wait_for_alert(tab: Tab) -> str:
    tab.page.wait_for_function(ALERT_SHOWN_JS, timeout=TIMEOUT_MS)
    return tab.page.locator("#login-alert").inner_text().strip()


def _assert_token_field_empty_and_focused(tab: Tab) -> None:
    assert tab.page.input_value("#login-token") == ""
    assert tab.page.evaluate(ACTIVE_ID_JS) == "login-token"
    assert tab.page.locator("#login-form button[type=submit]").is_enabled()


def _assert_nothing_stored(tab: Tab) -> None:
    assert tab.page.evaluate(STORAGE_JS) == NOTHING_STORED
    assert tab.page.evaluate(INDEXED_DB_JS) == 0


# --- login through the real form ------------------------------------------------------------------


def test_the_real_form_logs_in_and_leaves_nothing_behind(
    tab: Tab, server: PanelServer, browser_name: str
) -> None:
    _goto(tab, "/panel/login")
    _log_in_with_the_form(tab, server)

    assert tab.page.locator('[role="tablist"]').count() == 1
    assert tab.cookie_names() == [SESSION_COOKIE]
    _assert_nothing_stored(tab)
    sent = server.door_headers("/panel/login")
    assert sent.get("origin") == server.origin
    assert "x-antifaz-csrf" not in sent  # the login is exempt from the token layer
    leaked = tab.shows(server.admin_token)
    assert not leaked, "the admin token is in the DOM, a URL or the console"
    _clean(tab, browser_name)


@pytest.mark.parametrize("lang", ["es", "en"])
def test_a_wrong_token_shows_the_message_in_the_page_language(
    tab: Tab, server: PanelServer, browser_name: str, lang: str
) -> None:
    wrong = secrets.token_hex(32)
    _goto(tab, f"/panel/login?lang={lang}")
    _submit(tab, wrong)

    assert _wait_for_alert(tab) == CATALOGS[lang].text("login.error.failed")
    _assert_token_field_empty_and_focused(tab)
    assert tab.page.locator("#login-alert").get_attribute("role") == "alert"
    assert tab.cookie_names() == []
    _assert_nothing_stored(tab)
    leaked = tab.shows(wrong)
    assert not leaked, "the typed token is in the DOM, a URL or the console"
    assert tab.page.url == f"{server.origin}/panel/login?lang={lang}"
    _clean(tab, browser_name, refused=1)


def test_an_empty_field_sends_nothing(tab: Tab, server: PanelServer) -> None:
    _goto(tab, "/panel/login?lang=es")
    tab.page.click("#login-form button[type=submit]")

    assert _wait_for_alert(tab) == CATALOGS["es"].text("login.error.empty")
    _assert_token_field_empty_and_focused(tab)
    assert server.door_headers("/panel/login") == {}  # no request reached the gateway


def test_too_many_failures_show_the_wait_in_seconds(tab: Tab, server: PanelServer) -> None:
    """The limit of ADR-0018 (5 failures a minute per client) through the form: the sixth try
    shows the catalog's message with the seconds of Retry-After. Six submits on loopback take a
    small fraction of the limiter's real 60 s window, so this is not a timing assertion."""
    _goto(tab, "/panel/login?lang=en")
    expected = re.escape(CATALOGS["en"].text("login.error.too_many")).replace(
        re.escape("{seconds}"), r"([1-9][0-9]*)"
    )
    failed = CATALOGS["en"].text("login.error.failed")
    for _ in range(5):
        tab.page.evaluate("() => { document.getElementById('login-alert').textContent = ''; }")
        _submit(tab, secrets.token_hex(32))
        assert _wait_for_alert(tab) == failed
    tab.page.evaluate("() => { document.getElementById('login-alert').textContent = ''; }")
    _submit(tab, secrets.token_hex(32))

    assert re.fullmatch(expected, _wait_for_alert(tab))
    _assert_token_field_empty_and_focused(tab)
    assert tab.cookie_names() == []


# --- language -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("locale", "lang"), [("es-ES", "es"), ("es", "es"), ("en-GB", "en"), ("fr-FR", "en")]
)
def test_the_browser_locale_picks_the_language(
    new_tab: NewTab, server: PanelServer, locale: str, lang: str
) -> None:
    tab = new_tab(locale=locale)
    _goto(tab, "/panel/login")

    assert tab.page.evaluate(LANG_JS) == lang
    assert tab.page.locator("h1").inner_text() == CATALOGS[lang].text("login.heading")
    assert tab.response is not None
    assert tab.response.header_value("content-language") == lang
    _log_in_with_the_form(tab, server)
    assert tab.page.evaluate(LANG_JS) == lang
    assert (
        tab.page.locator("#tab-live")
        .inner_text()
        .strip()
        .startswith(CATALOGS[lang].text("tab.live"))
    )


def test_the_selector_switches_the_language_and_stores_nothing(
    tab: Tab, server: PanelServer, browser_name: str
) -> None:
    _goto(tab, "/panel/login")  # en-US
    tab.page.click(".login-tools a[hreflang=es]")
    tab.page.wait_for_url(f"{server.origin}/panel/login?lang=es")

    assert tab.page.evaluate(LANG_JS) == "es"
    assert tab.page.locator(".login-tools a[aria-current=true]").get_attribute("hreflang") == "es"
    assert tab.cookie_names() == []
    _assert_nothing_stored(tab)
    _log_in_with_the_form(tab, server)  # the panel opens in the same language
    assert tab.page.url == f"{server.origin}/panel?lang=es"
    assert tab.page.evaluate(LANG_JS) == "es"
    tab.page.click(".app-header a[hreflang=en]")
    tab.page.wait_for_url(f"{server.origin}/panel?lang=en")
    assert tab.page.evaluate(LANG_JS) == "en"
    assert tab.cookie_names() == [SESSION_COOKIE]  # only the session: no language cookie
    _assert_nothing_stored(tab)
    _clean(tab, browser_name)


# --- tabs with the keyboard -----------------------------------------------------------------------


def _selected(tab: Tab) -> str:
    rows = tab.page.evaluate(SELECTED_JS)
    chosen = [row for row in rows if row[1] == "true"]
    assert len(chosen) == 1
    for tab_id, selected, index, hidden in rows:
        assert (index == 0) == (selected == "true"), tab_id  # roving tabindex
        assert hidden == (selected != "true"), tab_id  # only the chosen panel shows
    return str(chosen[0][0]).removeprefix("tab-")


def test_the_tabs_follow_the_keyboard_on_the_desktop(tab: Tab, server: PanelServer) -> None:
    _goto(tab, "/panel/login")
    _log_in_with_the_form(tab, server)
    tablist = tab.page.locator('[role="tablist"]')
    assert tablist.get_attribute("aria-orientation") == "vertical"
    tab.page.focus("#tab-playground")

    for key, expected in [
        ("ArrowDown", "live"),
        ("ArrowDown", "keys"),
        ("ArrowUp", "live"),
        ("ArrowRight", "live"),  # vertical: left and right do nothing
        ("End", "status"),
        ("ArrowDown", "playground"),  # wraps around
        ("ArrowUp", "status"),
        ("Home", "playground"),
    ]:
        tab.page.keyboard.press(key)
        assert _selected(tab) == expected, key
        assert tab.page.evaluate(ACTIVE_ID_JS) == f"tab-{expected}"
    tab.page.keyboard.press("4")
    assert _selected(tab) == "policies"
    assert tab.page.locator("#crumb-name").inner_text() == CATALOGS["en"].text("tab.policies")
    tab.page.keyboard.press("Control+2")  # with a modifier: not a shortcut
    assert _selected(tab) == "policies"
    tab.page.evaluate(
        "() => { const i = document.createElement('input'); i.id = 'probe';"
        " document.querySelector('main').appendChild(i); i.focus(); }"
    )
    tab.page.keyboard.press("2")  # typing in a field never switches the view
    assert _selected(tab) == "policies"
    assert "#" not in tab.page.url  # no URL hash


def test_the_number_keys_work_only_in_the_tabs_and_the_views(tab: Tab, server: PanelServer) -> None:
    """WCAG 2.1.4 (character key shortcuts): 1-5 act only while the focus is in the tab list or
    in the view area, never page-wide and never in a field."""
    _goto(tab, "/panel/login")
    _log_in_with_the_form(tab, server)

    tab.page.focus(".app-header a[hreflang=en]")  # the language selector
    tab.page.keyboard.press("3")
    assert _selected(tab) == "playground"
    tab.page.focus(".app-header .theme-btn")
    tab.page.keyboard.press("3")
    assert _selected(tab) == "playground"
    tab.page.evaluate("() => document.activeElement.blur()")  # the focus on the body
    tab.page.keyboard.press("3")
    assert _selected(tab) == "playground"
    tab.page.focus("[data-logout]:visible")
    tab.page.keyboard.press("3")
    assert _selected(tab) == "playground"

    tab.page.focus("#tab-playground")  # in the tab list
    tab.page.keyboard.press("3")
    assert _selected(tab) == "keys"
    tab.page.focus("#panel-keys")  # in the view area
    tab.page.keyboard.press("5")
    assert _selected(tab) == "status"


def test_the_tabs_go_sideways_on_a_phone(
    new_tab: NewTab, server: PanelServer, browser_name: str
) -> None:
    tab = new_tab(width=390)
    _goto(tab, "/panel/login")
    _log_in_with_the_form(tab, server)
    assert tab.page.locator('[role="tablist"]').get_attribute("aria-orientation") == "horizontal"
    tab.page.focus("#tab-playground")

    for key, expected in [
        ("ArrowRight", "live"),
        ("ArrowDown", "live"),  # horizontal: up and down do nothing
        ("ArrowLeft", "playground"),
        ("ArrowLeft", "status"),
        ("Home", "playground"),
    ]:
        tab.page.keyboard.press(key)
        assert _selected(tab) == expected, key
    for name in TABS:
        tab.page.click(f"#tab-{name}")
        assert _selected(tab) == name
    no_overflow = "() => document.documentElement.scrollWidth <= window.innerWidth"
    assert tab.page.evaluate(no_overflow)
    _clean(tab, browser_name)


# --- Salir and the back button --------------------------------------------------------------------


@pytest.mark.parametrize("width", [1280, 390])
def test_salir_logs_out_and_the_back_button_shows_no_panel(
    new_tab: NewTab, server: PanelServer, browser_name: str, width: int
) -> None:
    tab = new_tab(width=width)
    _goto(tab, "/panel/login?lang=es")
    _log_in_with_the_form(tab, server)
    tab.page.click("#tab-keys")
    # A second panel page in the history (/panel?lang=es, then /panel?lang=en), so that the
    # back button after logging out goes to a panel URL.
    phone = width <= 640
    if phone:
        tab.page.click("#settings-btn")  # on a phone, language and «Salir» are in this menu
    place = "#settings-menu" if phone else ".app-header"
    tab.page.click(f"{place} a[hreflang=en]")
    tab.page.wait_for_url(f"{server.origin}/panel?lang=en")
    if phone:
        tab.page.click("#settings-btn")
    buttons = tab.page.locator("[data-logout]:visible")
    assert buttons.count() == 1  # the sidebar's on the desktop, the settings menu's on a phone
    expected_place = "settings-menu" if phone else "app-sidebar-foot"
    assert buttons.evaluate(f"(b) => !!b.closest('.{expected_place}')")
    assert buttons.inner_text().strip() == CATALOGS["en"].text("logout")

    with tab.page.expect_response(f"{server.origin}/panel/logout") as answer:
        buttons.click()
    assert answer.value.status == 200
    tab.page.wait_for_url(f"{server.origin}/panel/login?lang=en")
    assert tab.cookie_names() == []
    assert server.door_headers("/panel/logout").get("x-antifaz-csrf") == "present"

    tab.page.go_back(wait_until="load", timeout=TIMEOUT_MS)  # to /panel?lang=es
    # The door answers the old panel URL with the login page (the HTML is no-store). This is what
    # keeps the panel away; panel.js's bfcache reload is tested apart (Playwright's Chromium has
    # no back-forward cache).
    tab.page.wait_for_url(re.compile(re.escape(server.origin) + r"/panel/login$"))
    tab.page.wait_for_load_state("load")
    assert tab.page.locator('[role="tablist"]').count() == 0, "the panel came back"
    assert tab.page.locator("meta[name=antifaz-csrf]").count() == 0
    _assert_nothing_stored(tab)
    assert tab.blocked == []


PAGESHOW_JS = """(persisted) => window.dispatchEvent(
  new PageTransitionEvent('pageshow', {persisted}))"""


def test_a_page_restored_from_the_back_forward_cache_reloads(tab: Tab, server: PanelServer) -> None:
    """Defence in depth: Playwright's Chromium runs without the back-forward cache and the pages
    are no-store, so the back-button test above passes on the door alone. Here the restore event
    is dispatched by hand: persisted=true reloads the page, persisted=false does nothing."""
    _goto(tab, "/panel/login")
    _log_in_with_the_form(tab, server)
    tab.page.evaluate("() => { window.__notReloaded = true; }")

    tab.page.evaluate(PAGESHOW_JS, False)
    assert tab.page.evaluate("() => window.__notReloaded === true")  # no reload

    with tab.page.expect_navigation(wait_until="load"):
        tab.page.evaluate(PAGESHOW_JS, True)
    assert tab.page.evaluate("() => window.__notReloaded === undefined")  # a new document
    assert tab.page.url == f"{server.origin}/panel"


# --- everything clicked: CSP and console stay clean -----------------------------------------------


@pytest.mark.parametrize("locale", ["es-ES", "en-US"])
def test_clicking_everything_keeps_the_csp_and_the_console_clean(
    new_tab: NewTab, server: PanelServer, browser_name: str, locale: str
) -> None:
    tab = new_tab(locale=locale)
    _goto(tab, "/panel/login")
    for _ in range(2):
        tab.page.click(".login-tools .theme-btn")
    _assert_nothing_stored(tab)
    _submit(tab, secrets.token_hex(32))
    _wait_for_alert(tab)
    _clean(tab, browser_name, refused=1)  # the refusal is allowed in the login phase only
    login_phase = len(tab.console)

    _log_in_with_the_form(tab, server)
    for name in TABS:
        tab.page.click(f"#tab-{name}")
    for key in ("1", "2", "3", "4", "5", "Home", "End"):
        tab.page.keyboard.press(key)
    for _ in range(2):
        tab.page.click(".app-header .theme-btn")
    tab.page.click(".app-header a[aria-current=true]")
    tab.page.wait_for_load_state("load")
    _clean(tab, browser_name, since=login_phase)

    tab.page.click("[data-logout]:visible")
    tab.page.wait_for_url(re.compile(re.escape(server.origin) + r"/panel/login"))
    _clean(tab, browser_name, since=login_phase)


def test_the_theme_button_toggles_and_stores_nothing(tab: Tab, server: PanelServer) -> None:
    _goto(tab, "/panel/login")
    _log_in_with_the_form(tab, server)  # light color scheme
    button = tab.page.locator(".app-header .theme-btn")
    english = CATALOGS["en"]
    assert tab.page.evaluate(THEME_JS) is None  # the system's until pressed
    assert button.get_attribute("aria-label") == english.text("theme.to_dark")

    button.click()
    assert tab.page.evaluate(THEME_JS) == "dark"
    assert button.get_attribute("aria-label") == english.text("theme.to_light")
    button.click()
    assert tab.page.evaluate(THEME_JS) == "light"
    _assert_nothing_stored(tab)
    assert tab.cookie_names() == [SESSION_COOKIE]
    tab.page.reload(wait_until="load")
    assert tab.page.evaluate(THEME_JS) is None  # nothing was kept


# --- cross-site: another site cannot log in or out --------------------------------------------
#
# The attacker (server.OtherSite) is a plain page without CSP on another loopback port, run
# twice: named localhost (another site than 127.0.0.1) and as 127.0.0.1 on its own port (the
# same site: the SameSite=Strict session cookie travels, only the door's origin check and the
# CSRF token stop it). Same loopback address space, so the browsers' own local-network rules
# do not step in first.
#
# Whether the JSON fetch's own POST leaves depends on the browser: with request interception on,
# Playwright answers CORS preflights itself in some engines, so that POST may reach the door.
# Either way the door must refuse every POST that comes from the other site: each one that
# arrives is a 403 (read on the gateway's side), and no session is made or closed.


def _open_other_site(new_tab: NewTab, other_site: OtherSite) -> Tab:
    tab = new_tab(extra_origin=other_site.origin)
    return tab


def _go_to_other_site(tab: Tab, other_site: OtherSite) -> None:
    tab.page.goto(f"{other_site.origin}/", wait_until="load")
    assert tab.page.title() == "another site"


def _attack(tab: Tab, server: PanelServer, path: str, body: str, form_name: str) -> None:
    """The three tries of another site, each awaited to its end. What the gateway answered is
    read on its side: the browser hides the no-cors answer (CORP same-origin) from the page."""
    url = server.origin + path
    json_result = tab.page.evaluate(XSITE_JSON_FETCH, [url, body])
    assert json_result == "blocked"  # no CORS answer, whether or not the POST left
    before = len(server.door_statuses(path))
    plain_result = tab.page.evaluate(XSITE_PLAIN_FETCH, [url, body])  # never in an assert
    assert plain_result in ("opaque", "blocked")  # blocked: the answer's CORP header
    assert len(server.door_statuses(path)) == before + 1  # this one did reach the door
    # Firefox says Origin: null for a no-cors request from a page without a referrer policy
    # that allows it; the others name the other site. Never the gateway's own origin.
    assert server.door_headers(path).get("origin") != server.origin
    with tab.page.expect_navigation(wait_until="commit"):
        tab.page.evaluate(XSITE_FORM, [url, form_name, '"}'])
    assert len(server.door_statuses(path)) == before + 2
    assert server.door_headers(path).get("origin") != server.origin


def _assert_every_post_refused(server: PanelServer, path: str) -> None:
    statuses = server.door_statuses(path)
    # the no-cors fetch and the form always reach the door; the JSON fetch's own POST only
    # where Playwright answered its preflight
    assert len(statuses) in (2, 3)
    assert set(statuses) == {403}


def test_another_site_cannot_log_in(
    new_tab: NewTab, server: PanelServer, other_site: OtherSite
) -> None:
    tab = _open_other_site(new_tab, other_site)
    _go_to_other_site(tab, other_site)
    body = '{"token": "' + server.admin_token + '"}'
    form_name = '{"token": "' + server.admin_token + '", "x": "'  # text/plain body = JSON

    _attack(tab, server, "/panel/login", body, form_name)

    _assert_every_post_refused(server, "/panel/login")
    assert tab.cookie_names() == []  # the right token, from another site: no session
    assert tab.blocked == []


def test_another_site_cannot_log_out(
    new_tab: NewTab, server: PanelServer, other_site: OtherSite
) -> None:
    tab = _open_other_site(new_tab, other_site)
    tab.goto("/panel/login")
    _log_in_with_the_form(tab, server)
    _go_to_other_site(tab, other_site)

    _attack(tab, server, "/panel/logout", "{}", '{"x": "')

    _assert_every_post_refused(server, "/panel/logout")
    tab.goto("/panel")  # the session is still open
    assert tab.page.url == f"{server.origin}/panel"
    assert tab.page.locator('[role="tablist"]').count() == 1
    assert tab.cookie_names() == [SESSION_COOKIE]
    assert tab.blocked == []


# --- 11c-3a: the skip link leaves no sliver -------------------------------------------------------

SKIP_JS = """() => { const r = document.querySelector('.skip').getBoundingClientRect();
  return {top: r.top, bottom: r.bottom, vh: window.innerHeight}; }"""


def _skip_link_hidden_then_shown(tab: Tab, where: str, text: str) -> None:
    tab.page.add_style_tag(content=f"html{{font-size:{text}}}")
    box = tab.page.evaluate(SKIP_JS)
    assert box["bottom"] <= 0, (where, box)  # not even a sliver above the page
    tab.page.focus(".skip")
    box = tab.page.evaluate(SKIP_JS)
    assert box["top"] >= 0 and box["bottom"] <= box["vh"], (where, box)
    tab.page.locator(".skip").blur()


@pytest.mark.parametrize("text", ["100%", "200%"])
@pytest.mark.parametrize("width", [1280, 390])
def test_the_skip_link_is_off_screen_until_focused(
    new_tab: NewTab, server: PanelServer, width: int, text: str
) -> None:
    tab = new_tab(width=width, bypass_csp=True)
    tab.goto("/panel/login")
    _skip_link_hidden_then_shown(tab, "login", text)
    _log_in_with_the_form(tab, server)
    _skip_link_hidden_then_shown(tab, "panel", text)
