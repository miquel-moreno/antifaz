"""The panel under forced colours (Windows high contrast), issue 53, PR 11c-4.

In `forced-colors: active` the browser drops every `box-shadow` and replaces the author's
colours with the system's. The panel drew its focus ring only with `box-shadow` (and set
`outline: none`), so no element showed where the focus was (WCAG 2.4.7); dots, pills, the
selected tab, the current language and the theme switch told their state by colour or shadow
only. Here, in a context with `forced_colors="active"`:

- every focusable element of the login and of the panel (desktop and phone; the phone's
  settings menu open; «Estado» loaded), when focused from the keyboard, has an outline of 2 px
  or more; where the engine really forces the colours (Chromium, Firefox), that outline is not
  transparent;
- in normal mode the same elements keep today's ring (a `box-shadow`) and nothing else shows:
  their outline is transparent;
- the states keep a cue that is not colour: each status pill has its word, each dot sits with
  its text and is drawn in the text colour, the current NER state shows its «now» tag, the
  selected tab and the current language look different from the others, the switch keeps a
  visible track and thumb, and the phone tab bar's fade (a mask) is off;
- axe finds nothing (es and en, desktop and phone).

Playwright 1.63's WebKit for Windows matches `(forced-colors: active)` but does not force any
colour (a `box-shadow` stays, a transparent outline stays transparent), so there the checks
that need real forcing are replaced by "the ring is still drawn"; the rules of the
`@media (forced-colors: active)` block are still checked to apply.
"""

import re

import pytest

from tests.browser.harness import TIMEOUT_MS, NewTab, Tab
from tests.browser.server import PanelServer, ServerOptions
from tests.browser.test_panel_text_size import SETTLE_JS

pytestmark = pytest.mark.browser

# Engines whose forced-colours emulation really replaces the colours (see the docstring).
FORCING = {"chromium", "firefox"}
IDLE_JS = """() =>
  document.getElementById('status-refresh').getAttribute('aria-disabled') !== 'true' &&
  document.getElementById('status-time').textContent !== ''"""

# Every focusable element under `root` that is rendered, kept on window so each one is focused in
# turn. The main landmark (#main, tabindex=-1) is left out: only the skip link sends the focus
# there and it is not a control.
COLLECT_JS = """(root) => {
  const sel = 'a[href], button, input:not([type=hidden]), select, textarea, [tabindex]';
  const all = document.querySelector(root).querySelectorAll(sel);
  window.__antifazFocusable = [...all].filter((el) =>
    el.id !== 'main' && !el.closest('[hidden]') && el.getClientRects().length > 0 &&
    getComputedStyle(el).visibility !== 'hidden');
  return window.__antifazFocusable.length;
}"""
FOCUS_JS = """(i) => {
  const el = window.__antifazFocusable[i];
  el.focus();
  const s = getComputedStyle(el);
  const name = el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') +
    (typeof el.className === 'string' && el.className ? '.' + el.className.trim()
      .split(/\\s+/).join('.') : '') + ' "' + (el.textContent || '').trim().slice(0, 16) + '"';
  return {name, visible: el.matches(':focus-visible'), active: document.activeElement === el,
          outlineStyle: s.outlineStyle, outlineWidth: parseFloat(s.outlineWidth) || 0,
          outlineColor: s.outlineColor, boxShadow: s.boxShadow};
}"""
ALPHA = re.compile(r"rgba?\(([^)]*)\)")


def _alpha(colour: str) -> float:
    """The alpha of a computed rgb()/rgba() colour; "transparent" is 0."""
    if colour == "transparent":
        return 0.0
    match = ALPHA.match(colour)
    if not match:
        return 1.0
    parts = [part.strip() for part in re.split(r"[,/ ]+", match.group(1)) if part.strip()]
    return float(parts[3]) if len(parts) > 3 else 1.0


@pytest.fixture
def server_options() -> ServerOptions:
    return ServerOptions(provider_keys=True, ner_state="ok")


def _locale(lang: str) -> str:
    return "es-ES" if lang == "es" else "en-US"


def _keyboard_start(tab: Tab) -> None:
    """Tab once from the page, so the focus modality is the keyboard's: every engine then
    shows :focus-visible on the elements focused by script after it."""
    tab.page.evaluate("() => document.activeElement && document.activeElement.blur()")
    tab.page.keyboard.press("Tab")


def _log_in(tab: Tab, server: PanelServer, lang: str) -> None:
    tab.goto(f"/panel/login?lang={lang}")
    tab.page.fill("#login-token", server.admin_token)
    tab.page.click("#login-form button[type=submit]")
    tab.page.wait_for_url(f"{server.origin}/panel?lang={lang}")
    tab.page.evaluate(SETTLE_JS)
    tab.remember_secrets()


def _open_status_by_keyboard(tab: Tab) -> None:
    """«Estado» is the last tab: End from the first one, so the modality stays the keyboard's."""
    _keyboard_start(tab)
    tab.page.focus("#tab-playground")
    tab.page.keyboard.press("End")
    tab.page.wait_for_function(IDLE_JS, timeout=TIMEOUT_MS)


def _open_menu_by_keyboard(tab: Tab) -> None:
    tab.page.focus("#settings-btn")
    tab.page.keyboard.press("Enter")
    tab.page.locator("#settings-menu").wait_for(state="visible")


def _rings(tab: Tab, root: str = "body") -> list[dict[str, object]]:
    count: int = tab.page.evaluate(COLLECT_JS, root)
    assert count >= 3  # the walk found the page's (or the menu's) controls
    return [tab.page.evaluate(FOCUS_JS, i) for i in range(count)]


def _missing_forced_ring(rings: list[dict[str, object]], browser_name: str) -> list[str]:
    bad = []
    for ring in rings:
        if not ring["visible"] or not ring["active"]:
            bad.append(f"{ring['name']}: not focused from the keyboard {ring}")
        elif ring["outlineStyle"] == "none" or float(str(ring["outlineWidth"])) < 2:
            outline = f"{ring['outlineStyle']} {ring['outlineWidth']}"
            bad.append(f"{ring['name']}: no outline ({outline})")
        elif browser_name in FORCING and _alpha(str(ring["outlineColor"])) == 0:
            bad.append(f"{ring['name']}: transparent outline {ring['outlineColor']}")
        elif browser_name not in FORCING and ring["boxShadow"] == "none":
            bad.append(f"{ring['name']}: no ring at all")
    return bad


def _changed_normal_ring(rings: list[dict[str, object]]) -> list[str]:
    bad = []
    for ring in rings:
        if not ring["visible"] or not ring["active"]:
            bad.append(f"{ring['name']}: not focused from the keyboard {ring}")
        elif ring["boxShadow"] == "none":
            bad.append(f"{ring['name']}: lost its box-shadow ring")
        elif ring["outlineStyle"] != "none" and _alpha(str(ring["outlineColor"])) > 0:
            bad.append(f"{ring['name']}: a visible outline {ring['outlineColor']}")
    return bad


def _walk_every_control(tab: Tab, server: PanelServer, lang: str, width: int) -> list[str]:
    """The rings of every control: login, panel (first view), «Estado» loaded, phone menu."""
    tab.goto(f"/panel/login?lang={lang}")
    _keyboard_start(tab)
    rings = [{**ring, "name": "login " + str(ring["name"])} for ring in _rings(tab)]
    _log_in(tab, server, lang)
    _keyboard_start(tab)
    rings += [{**ring, "name": "panel " + str(ring["name"])} for ring in _rings(tab)]
    _open_status_by_keyboard(tab)
    rings += [{**ring, "name": "status " + str(ring["name"])} for ring in _rings(tab)]
    if width < 768:
        _open_menu_by_keyboard(tab)
        # Only the menu's own controls: the menu closes when the focus leaves it.
        menu = _rings(tab, "#settings-menu")
        rings += [{**ring, "name": "menu " + str(ring["name"])} for ring in menu]
    return rings


@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize("lang", ["es", "en"])
def test_every_control_shows_its_focus_under_forced_colours(
    new_tab: NewTab, server: PanelServer, browser_name: str, lang: str, width: int
) -> None:
    tab = new_tab(width=width, locale=_locale(lang), forced_colors="active")
    rings = _walk_every_control(tab, server, lang, width)
    bad = _missing_forced_ring(rings, browser_name)
    assert bad == [], "\n".join(bad)


@pytest.mark.parametrize("width", [1280, 390])
def test_in_normal_mode_the_ring_is_todays(
    new_tab: NewTab, server: PanelServer, width: int
) -> None:
    tab = new_tab(width=width, locale="es-ES")
    rings = _walk_every_control(tab, server, "es", width)
    bad = _changed_normal_ring(rings)
    assert bad == [], "\n".join(bad)


# Each state cue of the loaded «Estado» view and the chrome, read without any focus.
CUES_JS = """() => {
  const shown = (el) => !el.closest('[hidden]') && el.getClientRects().length > 0;
  const own = (el) => [...el.childNodes].filter((n) => n !== el.querySelector('.dot'))
    .map((n) => n.textContent).join('').trim();
  const look = (el) => { const s = getComputedStyle(el); return [s.backgroundColor,
    s.borderTopStyle + ' ' + s.borderTopWidth + ' ' + s.borderTopColor,
    s.outlineStyle + ' ' + s.outlineWidth + ' ' + s.outlineColor,
    s.textDecorationLine].join('|'); };
  const pills = [...document.querySelectorAll('.status')].filter(shown)
    .map((p) => ({id: p.id, label: p.querySelector('[data-label]').textContent.trim()}));
  const dots = [...document.querySelectorAll('.dot')].filter(shown).map((d) => {
    const s = getComputedStyle(d);
    return {where: (d.parentElement.id || d.parentElement.className),
            text: own(d.parentElement), background: s.backgroundColor, colour: s.color};
  });
  const now = [...document.querySelectorAll('.ner li')].filter((li) =>
    shown(li.querySelector('.tag')) && li.querySelector('.tag').textContent.trim() !== '');
  const tabs = [...document.querySelectorAll('[role=tab]')].filter(shown);
  const selected = tabs.find((t) => t.getAttribute('aria-selected') === 'true');
  const other = tabs.find((t) => t.getAttribute('aria-selected') !== 'true');
  const links = [...document.querySelectorAll('.langs-link')].filter(shown);
  const current = links.find((a) => a.getAttribute('aria-current') === 'true');
  const plain = links.find((a) => a.getAttribute('aria-current') !== 'true');
  const list = document.querySelector('[role=tablist]');
  list.classList.add('is-more-start', 'is-more-end');
  const ls = getComputedStyle(list);
  const mask = [ls.maskImage, ls.webkitMaskImage].filter((v) => v !== undefined);
  list.classList.remove('is-more-start', 'is-more-end');
  return {pills, dots, now: now.length,
          tab: [look(selected), look(other)], lang: current ? [look(current), look(plain)] : null,
          mask};
}"""
SWITCH_JS = """() => {
  const track = getComputedStyle(document.querySelector('.settings-menu .switch-track'));
  const thumb = getComputedStyle(document.querySelector('.settings-menu .switch-thumb'));
  return {checked: document.querySelector('.settings-menu .theme-switch')
            .getAttribute('aria-checked'),
          trackBorder: parseFloat(track.borderTopWidth) || 0, trackBorderColour:
          track.borderTopColor, track: track.backgroundColor, thumb: thumb.backgroundColor};
}"""


@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize("lang", ["es", "en"])
def test_every_state_keeps_a_cue_that_is_not_colour(
    new_tab: NewTab, server: PanelServer, browser_name: str, lang: str, width: int
) -> None:
    tab = new_tab(width=width, locale=_locale(lang), forced_colors="active")
    _log_in(tab, server, lang)
    _open_status_by_keyboard(tab)
    tab.page.evaluate("() => document.activeElement && document.activeElement.blur()")
    cues = tab.page.evaluate(CUES_JS)

    assert len(cues["pills"]) >= 4  # the gateway, the NER and both providers
    assert [pill for pill in cues["pills"] if not pill["label"]] == []
    assert len(cues["dots"]) >= 4
    assert [dot for dot in cues["dots"] if not dot["text"]] == []  # never a dot alone
    assert cues["now"] == 1  # the current NER state says so in words
    assert cues["tab"][0] != cues["tab"][1]  # the selected tab looks different
    if cues["lang"] is not None:  # the desktop header (the phone's is in the menu)
        assert cues["lang"][0] != cues["lang"][1]
    assert cues["mask"] and all(value == "none" for value in cues["mask"])  # no fade
    if browser_name in FORCING:  # a dot is drawn in its text's colour, not in Canvas
        undrawn = [dot for dot in cues["dots"] if dot["background"] != dot["colour"]]
        assert undrawn == []

    if width < 768:
        _open_menu_by_keyboard(tab)
        menu = tab.page.evaluate(CUES_JS)
        assert menu["lang"] is not None and menu["lang"][0] != menu["lang"][1]
        states = []
        for _ in ("off", "on"):  # the switch as it is, then pressed (Enter keeps the focus)
            switch = tab.page.evaluate(SWITCH_JS)
            states.append(switch["checked"])
            assert switch["trackBorder"] >= 1, switch
            assert _alpha(switch["trackBorderColour"]) > 0 or browser_name not in FORCING, switch
            assert switch["thumb"] != switch["track"], switch
            tab.page.focus(".settings-menu .theme-switch")
            tab.page.keyboard.press("Enter")
        assert states[0] != states[1]  # both looks were measured


@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize("lang", ["es", "en"])
def test_axe_finds_nothing_under_forced_colours(
    new_tab: NewTab, server: PanelServer, lang: str, width: int
) -> None:
    tab = new_tab(width=width, locale=_locale(lang), forced_colors="active", bypass_csp=True)
    tab.goto(f"/panel/login?lang={lang}")
    report = tab.axe()
    assert report.passes > 0
    assert report.violations == "", f"login, incomplete rules {report.incomplete}"
    _log_in(tab, server, lang)
    _open_status_by_keyboard(tab)
    report = tab.axe()
    assert report.passes > 0
    assert report.violations == "", f"status, incomplete rules {report.incomplete}"
