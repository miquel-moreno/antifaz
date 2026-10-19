"""Every text of the panel follows the browser's text size (WCAG 1.4.4), 11c-3a review.

Miquel's review of the 200 % captures: the card text grew, but the view title, its description
and the «Antifaz» wordmark did not (they were in px). Here the text size is set to 200 % the same
way as the other 200 % tests (a style tag on the root, in a `bypass_csp` context), and every
element that holds text of its own (a non-blank text node as a direct child) must have a computed
font-size at least 1.8 times the one it had at 100 %: on the login and on the panel (the five
views, the settings menu and everything hidden included, since the computed style exists for
them too), in Spanish and English, at 1280 and 390 px, in Chromium, Firefox and WebKit.

No allow-list: nothing is exempt. A failure lists each offender with its two sizes.
"""

import pytest

from tests.browser.harness import NewTab, Tab
from tests.browser.server import PanelServer

pytestmark = pytest.mark.browser

# Each element with text of its own, outside <head>, <template>, <script>, <style> and the sprite:
# a short description and its computed font-size in px. Kept on window so the second reading is
# of the very same elements.
SIZES_JS = """(first) => {
  if (first) {
    const skip = 'head, template, script, style, svg';
    window.__antifazText = [...document.body.querySelectorAll('*')].filter((el) =>
      !el.closest(skip) &&
      [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim() !== ''));
  }
  return window.__antifazText.map((el) => {
    const name = el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') +
      (el.className && typeof el.className === 'string' ? '.' + el.className.trim()
        .split(/\\s+/).join('.') : '');
    const text = [...el.childNodes].filter((n) => n.nodeType === 3)
      .map((n) => n.textContent.trim()).join(' ').slice(0, 24);
    return {name: name + ' "' + text + '"', size: parseFloat(getComputedStyle(el).fontSize)};
  });
}"""
TEXT_200_CSS = "html{font-size:200%}"
SETTLE_JS = "() => document.fonts.ready.then(() => true)"


def _locale(lang: str) -> str:
    return "es-ES" if lang == "es" else "en-US"


def _shrunk(tab: Tab) -> list[str]:
    """The elements whose text grows less than 1.8x when the root text size doubles."""
    before = tab.page.evaluate(SIZES_JS, True)
    assert len(before) >= 8  # the walk found the page's texts
    tab.page.add_style_tag(content=TEXT_200_CSS)
    tab.page.evaluate(SETTLE_JS)
    after = tab.page.evaluate(SIZES_JS, False)
    return [
        f"{old['name']}: {old['size']}px -> {new['size']}px"
        for old, new in zip(before, after, strict=True)
        if new["size"] < 1.8 * old["size"]
    ]


@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize("lang", ["es", "en"])
def test_every_text_grows_with_the_text_size(
    new_tab: NewTab, server: PanelServer, lang: str, width: int
) -> None:
    tab = new_tab(width=width, bypass_csp=True, locale=_locale(lang))
    tab.goto(f"/panel/login?lang={lang}")
    shrunk = ["login: " + line for line in _shrunk(tab)]

    tab.goto(f"/panel/login?lang={lang}")  # a fresh page at 100 %
    tab.page.fill("#login-token", server.admin_token)
    tab.page.click("#login-form button[type=submit]")
    tab.page.wait_for_url(f"{server.origin}/panel?lang={lang}")
    tab.page.evaluate(SETTLE_JS)
    tab.remember_secrets()
    shrunk += ["panel: " + line for line in _shrunk(tab)]
    assert shrunk == [], "\n".join(shrunk)


# The sidebar's «processed on your network» line: the vertical centre of its dot, and the box of
# the first line of its text (a Range over the text node: one rect per line).
LOCAL_DOT_JS = """() => {
  const line = document.querySelector('.app-sidebar-foot .app-local');
  const dot = line.querySelector('.dot').getBoundingClientRect();
  const text = [...line.childNodes].find((n) => n.nodeType === 3 && n.textContent.trim());
  const range = document.createRange();
  range.selectNodeContents(text);
  const rects = [...range.getClientRects()].filter((r) => r.width > 0);
  const tops = [...new Set(rects.map((r) => Math.round(r.top)))];
  const first = rects.filter((r) => Math.round(r.top) === tops[0]);
  return {centre: dot.top + dot.height / 2, lines: tops.length,
          top: Math.min(...first.map((r) => r.top)),
          bottom: Math.max(...first.map((r) => r.bottom))};
}"""


@pytest.mark.parametrize("text_200", [False, True], ids=["text-100", "text-200"])
def test_the_sidebar_dot_sits_on_the_first_line(
    new_tab: NewTab, server: PanelServer, text_200: bool
) -> None:
    """Miquel's review: the green dot of «Los datos personales se detectan y se cambian en tu
    red» goes with the first line of the text, not with the middle of the block."""
    tab = new_tab(width=1280, bypass_csp=True, locale="es-ES")
    tab.goto("/panel/login?lang=es")
    tab.page.fill("#login-token", server.admin_token)
    tab.page.click("#login-form button[type=submit]")
    tab.page.wait_for_url(f"{server.origin}/panel?lang=es")
    tab.remember_secrets()
    if text_200:
        tab.page.add_style_tag(content=TEXT_200_CSS)
    tab.page.evaluate(SETTLE_JS)

    dot = tab.page.evaluate(LOCAL_DOT_JS)

    assert dot["lines"] >= 2, dot  # it wraps at this width: the check means something
    assert dot["top"] <= dot["centre"] <= dot["bottom"], dot


LAYOUT_JS = """() => {
  const button = document.getElementById('settings-btn');
  return {overflow: document.documentElement.scrollWidth - window.innerWidth,
          topBar: document.querySelector('.app-sidebar').getBoundingClientRect().height,
          scrollPaddingTop: parseFloat(getComputedStyle(document.documentElement)
            .scrollPaddingTop) || 0,
          phone: button.offsetParent !== null};
}"""
PAGE_OVERFLOW_JS = "() => document.documentElement.scrollWidth - window.innerWidth"


@pytest.mark.parametrize("width", [1280, 390, 320])
@pytest.mark.parametrize("lang", ["es", "en"])
def test_at_twice_the_text_size_nothing_spills_or_hides(
    new_tab: NewTab, server: PanelServer, lang: str, width: int
) -> None:
    """At 200 % the page never scrolls sideways, on a phone the sticky top bar stays within the
    scroll padding kept for it (a focused element is never under it, WCAG 2.4.11), and axe
    finds nothing."""
    tab = new_tab(width=width, bypass_csp=True, locale=_locale(lang))
    tab.goto(f"/panel/login?lang={lang}")
    tab.page.add_style_tag(content=TEXT_200_CSS)
    tab.page.evaluate(SETTLE_JS)
    assert tab.page.evaluate(PAGE_OVERFLOW_JS) <= 0
    tab.goto(f"/panel/login?lang={lang}")
    tab.page.fill("#login-token", server.admin_token)
    tab.page.click("#login-form button[type=submit]")
    tab.page.wait_for_url(f"{server.origin}/panel?lang={lang}")
    tab.remember_secrets()
    tab.page.add_style_tag(content=TEXT_200_CSS)
    tab.page.evaluate(SETTLE_JS)
    layout = tab.page.evaluate(LAYOUT_JS)
    assert layout["overflow"] <= 0, layout
    if layout["phone"]:
        assert layout["topBar"] <= layout["scrollPaddingTop"], layout
    report = tab.axe()
    assert report.passes > 0
    assert report.violations == "", f"incomplete rules {report.incomplete}"
