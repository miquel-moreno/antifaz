"""«Prueba un texto»: the life of a text and of an answer (issue 53, PR 11d-b, review 1).

Chromium, Firefox and WebKit, on the gateway with the fake NER (invented names).

- The wipe happens BEFORE the page leaves: with a real result on screen, the expiry, a 401 and
  «Salir» empty the field, the three panes, the meta line, the callouts and the live region while
  the page is still there (the navigation to the login is held by a route to look at it).
- An answer that arrives after the wipe never fills the page again, and one that arrives after
  the text changed is dropped (so «no usado» never describes a text that was not tried).
- A new try that fails (413 or any error) clears the previous result: it was another text.
- «Nombres no comprobados» in the meta line has the warning colour of its callout.
- The panes show that they scroll (scroll shadows that end at the end) and take the focus.
- The word estimate follows the server's tokens for scripts without capitals (Arabic, Hebrew,
  Thai, Devanagari) closely, never a gross over-count.
"""

import contextlib
import json
import re

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Route

from antifaz.detect.ner.chunker import token_count
from antifaz.detect.ner.view import ner_view
from antifaz.detect.normalize import normalize
from antifaz.web.i18n import load_catalogs
from tests.browser.harness import TIMEOUT_MS, Tab
from tests.browser.server import PanelServer, ServerOptions
from tests.browser.test_panel_try import (
    ERROR_SHOWN_JS,
    IDLE_JS,
    TRY_PATH,
    hidden,
    log_in,
    text_of,
    try_text,
    wait_result,
)

pytestmark = pytest.mark.browser

CATALOGS = load_catalogs()
LOGIN = re.compile(r".*/panel/login.*")
EMPTIED_JS = """() => ({
  field: document.getElementById('try-text').value,
  panes: ['try-input', 'try-sent', 'try-reply', 'try-meta', 'try-live']
    .map((id) => document.getElementById(id).textContent).join(''),
  shown: ['try-result', 'try-names', 'try-error', 'try-too-long']
    .filter((id) => { const e = document.getElementById(id); return e && !e.hidden; }),
})"""
EMPTY = {"field": "", "panes": "", "shown": []}
TEXT = "Escribe a lola.inventada@example.org"


@pytest.fixture
def server_options() -> ServerOptions:
    return ServerOptions(ner_names=True)


def _hold(tab: Tab, pattern: object) -> list[Route]:
    held: list[Route] = []
    tab.page.route(pattern, lambda route: held.append(route))  # type: ignore[arg-type]
    return held


def _release(held: list[Route]) -> None:
    for route in held:
        with contextlib.suppress(PlaywrightError):  # the page or the request already gone
            route.abort()


def _wait_held(tab: Tab, held: list[Route]) -> None:
    for _ in range(300):
        if held:
            return
        tab.page.wait_for_timeout(50)
    raise AssertionError("the request never came")


def _result(tab: Tab) -> None:
    try_text(tab, TEXT)
    wait_result(tab)
    assert text_of(tab, "try-input") == TEXT


@pytest.mark.parametrize("way", ["expiry", "unauthorized", "logout"])
def test_every_way_out_wipes_before_the_page_leaves(
    tab: Tab, server: PanelServer, way: str
) -> None:
    log_in(tab, server)
    _result(tab)
    held = _hold(tab, LOGIN)

    if way == "logout":
        tab.page.click(".app-sidebar-foot [data-logout]")
    elif way == "unauthorized":
        tab.page.evaluate("() => document.dispatchEvent(new CustomEvent('antifaz:unauthorized'))")
    else:
        tab.page.clock.install()
        tab.page.evaluate(
            "() => document.dispatchEvent(new Event('visibilitychange'))"
        )  # session.js recomputes on the fake clock
        tab.page.clock.run_for(31 * 60 * 1000)
    _wait_held(tab, held)

    assert tab.page.evaluate(EMPTIED_JS) == EMPTY  # still the panel page, already empty
    _release(held)


def test_an_answer_after_the_wipe_never_fills_the_page(tab: Tab, server: PanelServer) -> None:
    log_in(tab, server)
    answers = _hold(tab, f"{tab.origin}{TRY_PATH}")
    tab.page.fill("#try-text", TEXT)
    tab.page.click("#try-submit")
    _wait_held(tab, answers)
    leaving = _hold(tab, LOGIN)

    tab.page.evaluate("() => document.dispatchEvent(new CustomEvent('antifaz:unauthorized'))")
    _wait_held(tab, leaving)
    with contextlib.suppress(PlaywrightError):  # aborted by the page: even better
        answers[0].fulfill(status=200, headers={"Content-Type": "application/json"}, body=_ok())
    tab.page.wait_for_timeout(300)  # let any late handler run

    assert tab.page.evaluate(EMPTIED_JS) == EMPTY
    _release(leaving)


def _ok(names_checked: bool = True, length: int = len(TEXT)) -> str:
    return json.dumps(
        {
            "outcome": "masked",
            "blocked_reason": None,
            "length_utf16": length,
            "entities": [],
            "sent": [{"text": "x"}],
            "reply": [{"text": "y"}],
            "counts": [],
            "policy_version": "builtin-1",
            "ner": "ok",
            "names_checked": names_checked,
        }
    )


def test_an_answer_for_a_text_that_changed_is_dropped(tab: Tab, server: PanelServer) -> None:
    log_in(tab, server)
    try_text(tab, "palabra " * 600)
    tab.page.locator("#try-too-long").wait_for(state="visible")
    answers = _hold(tab, f"{tab.origin}{TRY_PATH}")
    tab.page.click("#try-without-ner")
    _wait_held(tab, answers)

    tab.page.type("#try-text", "x")  # edited while the try without the detector runs
    answers[0].fulfill(
        status=200,
        headers={"Content-Type": "application/json"},
        body=_ok(names_checked=False, length=len("palabra " * 600)),
    )
    tab.page.wait_for_function(IDLE_JS, timeout=TIMEOUT_MS)

    assert text_of(tab, "try-words") != CATALOGS["es"].text("try.count.words_not_used")
    assert hidden(tab, "try-result") and hidden(tab, "try-names")


@pytest.mark.parametrize("failure", ["too_long", "server_error"])
def test_a_failed_new_try_clears_the_previous_result(
    tab: Tab, server: PanelServer, failure: str
) -> None:
    log_in(tab, server)
    _result(tab)

    if failure == "too_long":
        try_text(tab, "palabra " * 600)
        tab.page.locator("#try-too-long").wait_for(state="visible")
    else:
        tab.page.route(
            f"{tab.origin}{TRY_PATH}",
            lambda route: route.fulfill(status=500, headers={"Content-Type": "application/json"}),
        )
        tab.page.click("#try-submit")
        tab.page.wait_for_function(ERROR_SHOWN_JS, timeout=TIMEOUT_MS)

    assert hidden(tab, "try-result")
    assert text_of(tab, "try-input") == "" and text_of(tab, "try-meta") == ""
    assert not hidden(tab, "try-empty")


def test_names_not_checked_in_the_meta_line_has_the_warning_colour(
    tab: Tab, server: PanelServer
) -> None:
    log_in(tab, server)
    try_text(tab, "palabra " * 600)
    tab.page.locator("#try-too-long").wait_for(state="visible")
    tab.page.click("#try-without-ner")
    wait_result(tab)

    colours = tab.page.evaluate(
        """(phrase) => {
          const span = [...document.querySelectorAll('#try-meta span')]
            .find((s) => s.textContent === phrase);
          return [span ? getComputedStyle(span).color : null,
                  getComputedStyle(document.querySelector('#try-names strong')).color,
                  getComputedStyle(document.getElementById('try-meta')).color];
        }""",
        CATALOGS["es"].text("try.names.title"),
    )
    assert colours[0] is not None
    assert colours[0] == colours[1] != colours[2]


def test_the_panes_show_that_they_scroll_and_take_the_focus(tab: Tab, server: PanelServer) -> None:
    log_in(tab, server)
    try_text(tab, "\n".join(f"L{n}" for n in range(300)))
    wait_result(tab)

    for pane in ("try-input", "try-sent", "try-reply"):
        style = tab.page.evaluate(
            """(id) => { const e = document.getElementById(id); const s = getComputedStyle(e);
              return [s.overflowY, s.backgroundAttachment, e.tabIndex]; }""",
            pane,
        )
        assert style[0] == "auto", pane
        assert "local" in style[1], pane  # the scroll shadows move with the content
        assert style[2] == 0, pane


SCRIPTS = {
    "arabic": "مرحبا بالعالم، هذا اختبار بسيط لنص مكتوب. ",
    "hebrew": "שלום עולם, זה מבחן פשוט של טקסט כתוב. ",
    "thai": "สวัสดีชาวโลก นี่คือการทดสอบข้อความ ",
    "devanagari": "नमस्ते दुनिया, यह लिखे गए पाठ की एक सरल परीक्षा है। ",
}


@pytest.mark.parametrize("script", sorted(SCRIPTS))
def test_the_word_estimate_follows_the_server_for_other_scripts(
    tab: Tab, server: PanelServer, script: str
) -> None:
    text = SCRIPTS[script] * 6
    server_count = token_count(ner_view(normalize(text).text))
    log_in(tab, server)

    tab.page.fill("#try-text", text)
    shown = text_of(tab, "try-words")
    estimate = int(re.search(r"≈ (\d+)", shown).group(1))  # type: ignore[union-attr]

    assert abs(estimate - server_count) <= max(3, server_count // 10), (estimate, server_count)
