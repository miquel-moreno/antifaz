"""«Prueba un texto» in real browsers (issue 53, PR 11d-b, ADR-0018): Chromium, Firefox, WebKit.

The gateway of these tests has the fake NER of tests/nerfakes.py (invented names, in process)
unless a test says it is off; no provider is ever called (the fixture checks it).

- The example and Ctrl+Enter: the three panes with the WHOLE text (no "[…]"), the marks on the
  UTF-16 offsets the server gives (checked against the page's own length first), the markers,
  the values put back, the counts and the meta line; the live region gets counts only.
- The counters: always the bytes; with the NER on the estimated words (≈), only the warning
  colour past the server's number (no offer until the server says 413 text_too_long_for_ner).
  Too many bytes: nothing is sent and the message says why.
- The offer «Probar sin el detector de nombres» after the 413, with the focus on it; the result
  without the detector says «Nombres no comprobados» above the panes and in the meta line (one
  catalog text) and the word counter says «no usado» until the text changes. With the NER off for
  the gateway the callout has its own body.
- Every error has its fixed message; busy is a message at once and never a retry.
- A result whose length does not match the page's text marks nothing and says so.
- The text stays when switching tabs and is gone after a reload.
- The panes scroll inside and the keyboard scrolls them; on a phone (320, 390 px) one pane at a
  time and no horizontal scroll; axe finds nothing in any state, light and dark, es and en, under
  forced colours and at 200 % text.

No token, cookie value or page HTML inside an assert (tests/unit/test_browser_harness_rules.py).
"""

import json
import re
import sys
from collections.abc import Callable
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Route

from antifaz.api.app import TRY_NER_MAX_WORDS
from antifaz.web.i18n import load_catalogs
from antifaz.web.try_text import MAX_TEXT_BYTES
from tests.browser.console_rules import unexpected_console
from tests.browser.harness import TIMEOUT_MS, NewTab, Tab
from tests.browser.server import PanelServer, ServerOptions
from tests.browser.test_panel_text_size import SETTLE_JS, TEXT_200_CSS
from tests.nerfakes import CARMEN

pytestmark = pytest.mark.browser

CATALOGS = load_catalogs()
TRY_PATH = "/panel/api/try"
RESULT_SHOWN_JS = "() => !document.getElementById('try-result').hidden"
ERROR_SHOWN_JS = "() => !document.getElementById('try-error').hidden"
IDLE_JS = "() => document.getElementById('try-submit').getAttribute('aria-disabled') !== 'true'"
ACTIVE_ID_JS = "() => document.activeElement ? document.activeElement.id : null"
TEXT_OF_JS = "(id) => document.getElementById(id).textContent"
MARKS_JS = """() => [...document.querySelectorAll('#try-input mark.pii')].map((m) =>
  [m.textContent, m.getAttribute('data-text-type')])"""
MARKERS_JS = "() => [...document.querySelectorAll('#try-sent .marker')].map((m) => m.textContent)"
RESTORED_JS = (
    "() => [...document.querySelectorAll('#try-reply .restored')].map((m) => m.textContent)"
)
HIDDEN_JS = "(id) => { const e = document.getElementById(id); return !e || e.hidden; }"
CLASSES_JS = "(id) => [...document.getElementById(id).classList]"
PAGE_OVERFLOW_JS = "() => document.documentElement.scrollWidth - window.innerWidth"
VISIBLE_PANES_JS = """() => [...document.querySelectorAll('#try-panes .pane')]
  .filter((p) => p.getClientRects().length > 0).map((p) => p.classList[1])"""
SCROLL_JS = """(id) => { const e = document.getElementById(id);
  return [e.scrollHeight, e.clientHeight, e.scrollTop, getComputedStyle(e).overflowY]; }"""
FAILED_RESOURCE = re.compile(
    r"\[error\] Failed to load resource: the server responded with a status of [45][0-9][0-9]"
)


@pytest.fixture
def server_options() -> ServerOptions:
    return ServerOptions(ner_names=True)


def _t(lang: str, key: str, **values: object) -> str:
    return CATALOGS[lang].text(key, **values)


def _locale(lang: str) -> str:
    return "es-ES" if lang == "es" else "en-US"


def log_in(tab: Tab, server: PanelServer, lang: str = "es") -> None:
    tab.goto(f"/panel/login?lang={lang}")
    tab.page.fill("#login-token", server.admin_token)
    tab.page.click("#login-form button[type=submit]")
    tab.page.wait_for_url(f"{server.origin}/panel?lang={lang}")
    tab.page.evaluate(SETTLE_JS)
    tab.remember_secrets()


def text_of(tab: Tab, element_id: str) -> str:
    value: str = tab.page.evaluate(TEXT_OF_JS, element_id)
    return value


def hidden(tab: Tab, element_id: str) -> bool:
    value: bool = tab.page.evaluate(HIDDEN_JS, element_id)
    return value


def wait_result(tab: Tab) -> None:
    tab.page.wait_for_function(RESULT_SHOWN_JS, timeout=TIMEOUT_MS)
    tab.page.wait_for_function(IDLE_JS, timeout=TIMEOUT_MS)


def try_text(tab: Tab, text: str) -> None:
    tab.page.fill("#try-text", text)
    with tab.page.expect_response(lambda r: urlsplit(r.url).path == TRY_PATH):
        tab.page.click("#try-submit")
    tab.page.wait_for_function(IDLE_JS, timeout=TIMEOUT_MS)


def try_requests(tab: Tab) -> int:
    return len([url for url in tab.requests if urlsplit(url).path == TRY_PATH])


def clean(tab: Tab, browser_name: str, *, refused: int = 0) -> None:
    """No CSP violation, no page error or foreign request; in the console only the 4xx/5xx lines
    of the Try answers the test asked for on purpose (Chromium and WebKit log them)."""
    assert tab.csp_violations() == []
    lines = []
    for line, where in zip(tab.console, tab.console_locations, strict=True):
        if FAILED_RESOURCE.match(line) and urlsplit(where).path == TRY_PATH and refused > 0:
            refused -= 1
            continue
        lines.append(line)
    assert unexpected_console(lines, browser_name, sys.platform) == []
    assert tab.errors == []
    assert tab.blocked == []
    assert tab.foreign_requests() == []


def axe_clean(tab: Tab) -> None:
    report = tab.axe()
    assert report.violations == "", report.violations
    assert report.passes > 0


# --- the example and the three panes ----------------------------------------------------------


@pytest.mark.parametrize("lang", ["es", "en"])
def test_the_example_shows_the_whole_text_its_marks_markers_and_values(
    new_tab: NewTab, server: PanelServer, browser_name: str, lang: str
) -> None:
    tab = new_tab(locale=_locale(lang))
    log_in(tab, server, lang)
    example = _t(lang, "try.example.text")

    tab.page.click("#try-example")
    assert tab.page.input_value("#try-text") == example
    assert not hidden(tab, "try-example-note")
    with tab.page.expect_response(lambda r: urlsplit(r.url).path == TRY_PATH):
        tab.page.click("#try-submit")
    wait_result(tab)

    assert text_of(tab, "try-input") == example  # the whole text, exactly
    marks = tab.page.evaluate(MARKS_JS)
    labels = {_t(lang, f"try.type.{name}") for name in ("es_dni", "email", "iban", "phone")}
    assert {label for _, label in marks} == labels
    assert all(value in example for value, _ in marks)
    markers = tab.page.evaluate(MARKERS_JS)
    assert sorted(markers) == ["[[EMAIL_1]]", "[[ES_DNI_1]]", "[[IBAN_1]]", "[[PHONE_1]]"]
    sent = text_of(tab, "try-sent")
    assert all(value not in sent for value, _ in marks)
    assert sorted(tab.page.evaluate(RESTORED_JS)) == sorted(value for value, _ in marks)
    meta = text_of(tab, "try-meta")
    assert _t(lang, "try.meta.hidden", n=4) in meta
    assert _t(lang, "try.meta.local") in meta
    assert _t(lang, "try.names.title") not in meta  # the NER read it
    assert hidden(tab, "try-names") and hidden(tab, "try-empty")
    live = text_of(tab, "try-live")
    assert live == _t(lang, "try.meta.hidden", n=4)  # counts only, never a value
    assert all(value not in live for value, _ in marks)
    # The focus stays where the click left it (WebKit does not focus a clicked button: it
    # focuses the view, its nearest focusable ancestor); the result never takes it.
    assert tab.page.evaluate(ACTIVE_ID_JS) in ("try-submit", "panel-playground")
    clean(tab, browser_name)
    axe_clean(tab)
    tab.page.fill("#try-text", "Otro texto")  # the note is about the example only
    assert hidden(tab, "try-example-note")


def test_ctrl_enter_tries_the_text(tab: Tab, server: PanelServer, browser_name: str) -> None:
    log_in(tab, server)
    tab.page.fill("#try-text", "Escribe a ana.prueba@example.com")

    with tab.page.expect_response(lambda r: urlsplit(r.url).path == TRY_PATH):
        tab.page.press("#try-text", "Control+Enter")
    wait_result(tab)

    assert tab.page.evaluate(MARKERS_JS) == ["[[EMAIL_1]]"]
    assert tab.page.evaluate(ACTIVE_ID_JS) == "try-text"
    clean(tab, browser_name)


def test_marks_follow_utf16_offsets_after_emoji(tab: Tab, server: PanelServer) -> None:
    log_in(tab, server)
    thumb, family = chr(0x1F44D), chr(0x200D).join([chr(0x1F468), chr(0x1F469), chr(0x1F467)])
    text = f"{thumb}{family} correo ana.prueba@example.com {thumb}"

    try_text(tab, text)
    wait_result(tab)

    assert [value for value, _ in tab.page.evaluate(MARKS_JS)] == ["ana.prueba@example.com"]
    assert text_of(tab, "try-input") == text


# --- the counters ------------------------------------------------------------------------------


def test_the_counters_are_always_there_and_only_warn_past_the_words(
    tab: Tab, server: PanelServer, browser_name: str
) -> None:
    log_in(tab, server)
    words_prefix = _t("es", "try.count.words").split("{used}", 1)[0]
    assert text_of(tab, "try-bytes").endswith("bytes")
    assert text_of(tab, "try-words").startswith(words_prefix)
    assert str(TRY_NER_MAX_WORDS) in text_of(tab, "try-words")
    assert hidden(tab, "try-over")

    tab.page.fill("#try-text", "palabra " * (TRY_NER_MAX_WORDS + 5))

    assert "over" in tab.page.evaluate(CLASSES_JS, "try-words")
    assert not hidden(tab, "try-over")
    assert hidden(tab, "try-too-long")  # the offer waits for the server's answer
    assert try_requests(tab) == 0
    tab.page.fill("#try-text", "palabra " * 10)
    assert hidden(tab, "try-over")
    clean(tab, browser_name)


def test_too_many_bytes_are_never_sent(tab: Tab, server: PanelServer, browser_name: str) -> None:
    log_in(tab, server)
    tab.page.fill("#try-text", "a" * (MAX_TEXT_BYTES + 1))

    assert "over" in tab.page.evaluate(CLASSES_JS, "try-bytes")
    tab.page.click("#try-submit")
    tab.page.wait_for_function(ERROR_SHOWN_JS, timeout=TIMEOUT_MS)

    expected = _t("es", "try.error.too_large").replace("{max}", "20.480")
    assert text_of(tab, "try-error-text") == expected
    assert try_requests(tab) == 0
    clean(tab, browser_name)


# --- too long for the name detector, and a try without it --------------------------------------


def test_the_offer_comes_after_the_413_and_a_try_without_the_ner_says_so(
    tab: Tab, server: PanelServer, browser_name: str
) -> None:
    log_in(tab, server)
    text = "Hola. " + "palabra " * 600 + f"Firma: {CARMEN}, ana.prueba@example.com"

    try_text(tab, text)
    tab.page.locator("#try-too-long").wait_for(state="visible")

    assert tab.page.evaluate(ACTIVE_ID_JS) == "try-without-ner"  # the focus goes to the offer
    assert str(TRY_NER_MAX_WORDS) in text_of(tab, "try-too-long")
    assert hidden(tab, "try-result")
    with tab.page.expect_request(lambda r: urlsplit(r.url).path == TRY_PATH) as sent:
        tab.page.click("#try-without-ner")
    wait_result(tab)

    assert json.loads(sent.value.post_data or "{}")["ner"] is False
    title = _t("es", "try.names.title")
    assert not hidden(tab, "try-names")
    assert text_of(tab, "try-names-text") == _t("es", "try.names.body")
    assert title in text_of(tab, "try-names") and title in text_of(tab, "try-meta")
    assert hidden(tab, "try-too-long")
    assert text_of(tab, "try-words") == _t("es", "try.count.words_not_used")
    assert "over" not in tab.page.evaluate(CLASSES_JS, "try-words")
    # Nothing cut: the whole text, the name far down included, in the input and sent panes.
    assert text_of(tab, "try-input") == text
    assert CARMEN in text_of(tab, "try-sent")
    assert text_of(tab, "try-sent").count("palabra") == 600
    assert "[…]" not in text_of(tab, "try-sent") and "[...]" not in text_of(tab, "try-reply")
    assert title in text_of(tab, "try-live")
    tab.page.type("#try-text", "x")
    assert text_of(tab, "try-words") != _t("es", "try.count.words_not_used")
    clean(tab, browser_name, refused=1)


@pytest.mark.parametrize("server_options", [ServerOptions()], ids=["ner-off"])
def test_with_the_ner_off_the_callout_says_the_gateway_has_it_off(
    tab: Tab, server: PanelServer, browser_name: str
) -> None:
    log_in(tab, server)
    assert tab.page.locator("#try-words").count() == 0
    assert tab.page.locator("#try-too-long").count() == 0

    try_text(tab, "Escribe a ana.prueba@example.com")
    wait_result(tab)

    assert text_of(tab, "try-names-text") == _t("es", "try.names.off")
    assert _t("es", "try.names.title") in text_of(tab, "try-meta")
    clean(tab, browser_name)
    axe_clean(tab)


# --- errors ------------------------------------------------------------------------------------


def _fixed(
    status: int, code: str = "", headers: dict[str, str] | None = None
) -> Callable[..., None]:
    def answer(route: Route) -> None:
        body = json.dumps({"error": {"code": code, "message": "x"}}) if code else "{}"
        route.fulfill(
            status=status,
            headers={"Content-Type": "application/json", **(headers or {})},
            body=body,
        )

    return answer


ERRORS = [
    (_fixed(400, "invalid_try_body"), "try.error.bad_body", {}),
    (_fixed(403, "csrf_rejected"), "try.error.refused", {}),
    (_fixed(408, "body_timeout"), "try.error.timeout", {}),
    (_fixed(413, "text_too_large"), "try.error.too_large", {"max": "20.480"}),
    (_fixed(429, "too_many_tries", {"Retry-After": "7"}), "try.error.too_many", {"seconds": "7"}),
    (_fixed(429, "too_many_tries", {"Retry-After": "soon"}), "try.error.too_many_later", {}),
    (_fixed(503, "try_busy", {"Retry-After": "1"}), "try.error.try_busy", {}),
    (_fixed(503, "ner_busy", {"Retry-After": "1"}), "try.error.ner_busy", {}),
    (_fixed(500, "internal_error"), "try.error.internal", {}),
    (lambda route: route.abort(), "try.error.network", {}),
]


def test_every_error_has_its_fixed_message_and_busy_is_never_retried(
    tab: Tab, server: PanelServer
) -> None:
    log_in(tab, server)
    tab.page.fill("#try-text", "hola")
    for handler, key, values in ERRORS:
        tab.page.route(f"{tab.origin}{TRY_PATH}", handler)
        before = try_requests(tab)
        tab.page.click("#try-submit")
        tab.page.wait_for_function(ERROR_SHOWN_JS, timeout=TIMEOUT_MS)
        tab.page.wait_for_function(IDLE_JS, timeout=TIMEOUT_MS)
        tab.page.unroute(f"{tab.origin}{TRY_PATH}")

        expected = _t("es", key)
        for name, value in values.items():
            expected = expected.replace("{" + name + "}", value)
        assert text_of(tab, "try-error-text") == expected, key
        assert try_requests(tab) == before + 1, key  # one request, no retry
    assert tab.errors == [] and tab.csp_violations() == []
    axe_clean(tab)


def test_a_broken_character_is_the_400_message_without_a_request(
    tab: Tab, server: PanelServer
) -> None:
    log_in(tab, server)
    tab.page.evaluate("() => { document.getElementById('try-text').value = 'a\\ud800b'; }")

    tab.page.click("#try-submit")
    tab.page.wait_for_function(ERROR_SHOWN_JS, timeout=TIMEOUT_MS)

    assert text_of(tab, "try-error-text") == _t("es", "try.error.bad_body")
    assert try_requests(tab) == 0


def test_a_length_mismatch_marks_nothing_and_says_so(tab: Tab, server: PanelServer) -> None:
    log_in(tab, server)
    answer = {
        "outcome": "masked",
        "blocked_reason": None,
        "length_utf16": 999,
        "entities": [{"type": "EMAIL", "start": 0, "end": 4, "hidden": True}],
        "sent": [{"text": "hola"}],
        "reply": [{"text": "ok"}],
        "counts": [],
        "policy_version": "builtin-1",
        "ner": "ok",
        "names_checked": True,
    }
    tab.page.route(
        f"{tab.origin}{TRY_PATH}",
        lambda route: route.fulfill(
            status=200, headers={"Content-Type": "application/json"}, body=json.dumps(answer)
        ),
    )

    try_text(tab, "hola mundo")
    wait_result(tab)

    assert text_of(tab, "try-error-text") == _t("es", "try.mismatch")
    assert tab.page.evaluate(MARKS_JS) == []
    assert text_of(tab, "try-input") == "hola mundo"


@pytest.mark.parametrize("reason", ["detector_failed", "egress_guard"])
def test_a_blocked_answer_shows_the_blocked_note(
    tab: Tab, server: PanelServer, browser_name: str, reason: str
) -> None:
    log_in(tab, server)
    answer = {
        "outcome": "blocked",
        "blocked_reason": reason,
        "length_utf16": 4,
        "entities": [],
        "sent": [],
        "reply": [],
        "counts": [],
        "policy_version": "builtin-1",
        "ner": "ok",
        "names_checked": False,
    }
    tab.page.route(
        f"{tab.origin}{TRY_PATH}",
        lambda route: route.fulfill(
            status=200, headers={"Content-Type": "application/json"}, body=json.dumps(answer)
        ),
    )

    try_text(tab, "hola")
    wait_result(tab)

    assert not hidden(tab, "try-blocked")
    assert text_of(tab, "try-blocked-text") == _t("es", f"try.blocked.{reason}")
    assert text_of(tab, "try-blocked-code") == reason
    assert hidden(tab, "try-sent") and hidden(tab, "try-reply")
    assert not hidden(tab, "try-reply-blocked")
    assert hidden(tab, "try-names")  # blocked says more than «not checked»
    assert text_of(tab, "try-live") == _t("es", "try.blocked.title")
    clean(tab, browser_name)
    axe_clean(tab)


# --- the text's life ---------------------------------------------------------------------------


def test_the_text_stays_across_tabs_and_is_gone_after_a_reload(
    tab: Tab, server: PanelServer
) -> None:
    log_in(tab, server)
    text = "Escribe a lola.inventada@example.org"
    try_text(tab, text)
    wait_result(tab)

    tab.page.click("#tab-status")
    tab.page.click("#tab-playground")
    assert tab.page.input_value("#try-text") == text
    assert not hidden(tab, "try-result")

    tab.page.reload(wait_until="load")
    tab.page.evaluate(SETTLE_JS)
    assert tab.page.input_value("#try-text") == ""
    assert hidden(tab, "try-result")
    page = tab.page.content()
    assert "lola.inventada" not in page


# --- scrolling, phone, accessibility ----------------------------------------------------------


def test_the_panes_scroll_inside_and_the_keyboard_scrolls_them(
    tab: Tab, server: PanelServer
) -> None:
    log_in(tab, server)
    text = "\n".join(f"L{n}" for n in range(300))  # tall, but within the name detector's words
    try_text(tab, text)
    wait_result(tab)

    height, client, top, overflow = tab.page.evaluate(SCROLL_JS, "try-input")
    assert overflow == "auto" and height > client and top == 0
    tab.page.focus("#try-input")
    assert tab.page.evaluate(ACTIVE_ID_JS) == "try-input"
    tab.page.keyboard.press("ArrowDown", delay=10)
    tab.page.keyboard.press("End")
    tab.page.wait_for_function(
        "(id) => document.getElementById(id).scrollTop > 0", arg="try-input", timeout=TIMEOUT_MS
    )
    assert text_of(tab, "try-input") == text


@pytest.mark.parametrize("width", [320, 390])
@pytest.mark.parametrize("lang", ["es", "en"])
def test_on_a_phone_one_pane_at_a_time_and_no_horizontal_scroll(
    new_tab: NewTab, server: PanelServer, browser_name: str, width: int, lang: str
) -> None:
    tab = new_tab(width=width, locale=_locale(lang))
    log_in(tab, server, lang)
    tab.page.click("#try-example")
    with tab.page.expect_response(lambda r: urlsplit(r.url).path == TRY_PATH):
        tab.page.click("#try-submit")
    wait_result(tab)

    assert tab.page.evaluate(PAGE_OVERFLOW_JS) <= 0
    assert tab.page.evaluate(VISIBLE_PANES_JS) == ["p2"]  # «Recibe la IA» first
    tab.page.click(".pswitch button[data-p='1']")
    assert tab.page.evaluate(VISIBLE_PANES_JS) == ["p1"]
    assert tab.page.get_attribute(".pswitch button[data-p='1']", "aria-pressed") == "true"
    assert tab.page.evaluate(PAGE_OVERFLOW_JS) <= 0
    clean(tab, browser_name)
    axe_clean(tab)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_axe_finds_nothing_in_any_state(new_tab: NewTab, server: PanelServer, theme: str) -> None:
    tab = new_tab(color_scheme=theme)
    log_in(tab, server)
    axe_clean(tab)  # empty
    tab.page.click("#try-example")
    tab.page.click("#try-submit")
    wait_result(tab)
    axe_clean(tab)  # result
    try_text(tab, "palabra " * 600)
    tab.page.locator("#try-too-long").wait_for(state="visible")
    axe_clean(tab)  # the offer
    tab.page.click("#try-without-ner")
    wait_result(tab)
    tab.page.locator("#try-names").wait_for(state="visible")
    axe_clean(tab)  # names not checked
    tab.page.route(f"{tab.origin}{TRY_PATH}", _fixed(500, "internal_error"))
    tab.page.click("#try-submit")
    tab.page.wait_for_function(ERROR_SHOWN_JS, timeout=TIMEOUT_MS)
    axe_clean(tab)  # an error


def test_forced_colours_keep_marks_and_axe_finds_nothing(
    new_tab: NewTab, server: PanelServer
) -> None:
    tab = new_tab(forced_colors="active")
    log_in(tab, server)
    tab.page.click("#try-example")
    tab.page.click("#try-submit")
    wait_result(tab)

    outline = tab.page.evaluate(
        "() => getComputedStyle(document.querySelector('#try-input mark.pii')).outlineStyle"
    )
    assert outline == "solid"
    axe_clean(tab)


def test_at_twice_the_text_size_nothing_scrolls_sideways(
    new_tab: NewTab, server: PanelServer
) -> None:
    for width in (1280, 390):
        tab = new_tab(width=width, bypass_csp=True)
        log_in(tab, server)
        tab.page.add_style_tag(content=TEXT_200_CSS)
        tab.page.click("#try-example")
        tab.page.click("#try-submit")
        wait_result(tab)

        assert tab.page.evaluate(PAGE_OVERFLOW_JS) <= 0, width
        axe_clean(tab)


def test_only_the_keyboard_reaches_everything(tab: Tab, server: PanelServer) -> None:
    """Tab from the field reaches the example and «Probar»; Enter on «Probar» tries; Tab reaches
    each scrollable pane once a result is shown."""
    log_in(tab, server)
    tab.page.focus("#try-text")
    tab.page.keyboard.type("Escribe a ana.prueba@example.com")
    tab.page.keyboard.press("Tab")
    assert tab.page.evaluate(ACTIVE_ID_JS) == "try-example"
    tab.page.keyboard.press("Tab")
    assert tab.page.evaluate(ACTIVE_ID_JS) == "try-submit"
    with tab.page.expect_response(lambda r: urlsplit(r.url).path == TRY_PATH):
        tab.page.keyboard.press("Enter")
    wait_result(tab)

    reached = set()
    for _ in range(12):
        tab.page.keyboard.press("Tab")
        reached.add(tab.page.evaluate(ACTIVE_ID_JS))
    assert {"try-input", "try-sent", "try-reply"} <= reached
