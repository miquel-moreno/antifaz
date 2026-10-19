"""The panel's scripts, read as text (issue 53, PRs 11c-2b and 11d-b): static/login.js, panel.js,
try.js («Prueba un texto») and session.js (the expiry warning).

11d-b: each script is a standalone IIFE that talks to the others only through DOM events (it
never sets a global). Across every static script there is exactly ONE `setTimeout(`, in
session.js (the only timer: the next moment the warning or the expiry is due), and no
setInterval or requestAnimationFrame. Each script fetches only its own literal URL: try.js
"/panel/api/try", session.js "/panel/api/session". panel.js and try.js tell session.js about
every panel answer that counted as a use (not 401 or 403) with an "antifaz:activity" event.

- In the manifest, served byte for byte as text/javascript (web/assets.py).
- No visible text: every string literal is a selector, a key, an attribute or event name, a
  header, a fixed relative path or one of a short list of tokens. The words a person reads come
  from the page (a <template> or a data-text-* attribute), read with textContent.
- Never eval, Function, innerHTML and friends, document.write, storage (localStorage,
  sessionStorage, indexedDB, caches, cookies), console, absolute URLs, inline handlers (`.onx =`),
  inline styles (`.style`), template literals, timers or a fetch to a URL held in a variable.
- No comments (no served file carries one, test_web_assets.py): what the scripts do is written
  in docs/TECNICO.md ("Panel: idiomas, entrar y el esqueleto") and in these tests.
- "use strict" first. The login sends no X-Antifaz-CSRF (the door exempts it); the panel sends
  it on «Salir».

A small tokenizer splits each script into code, comments and string literals. The scripts use
no regular-expression literals and no template literals (a template literal is refused), so the
split is exact.
"""

import re

import pytest

from antifaz.web.assets import SERVED, STATIC_DIR

SCRIPTS = ("login.js", "panel.js", "try.js", "session.js")
# The only script with a timer (11d-b).
TIMER_SCRIPT = "session.js"
# The one URL each script may fetch (11d-b for the last two).
FETCHES = {
    "login.js": {"/panel/login"},
    "panel.js": {"/panel/logout", "/panel/api/status"},
    "try.js": {"/panel/api/try"},
    "session.js": {"/panel/api/session"},
}
ACTIVITY_EVENT = "antifaz:activity"
# A string literal that is all lowercase ASCII without spaces is a key, a selector, an attribute,
# an event or a relative path: never prose a person reads.
PLAIN_TOKEN = re.compile(r"""[a-z0-9_\-.#\[\]="':/?&{}()*>+,]*""")
# Anything else must be one of these.
ALLOWED_LITERALS = frozenset(
    {
        "use strict",
        "(prefers-color-scheme: dark)",
        "Content-Type",
        "Retry-After",
        "X-Antifaz-CSRF",
        "POST",
        "ArrowUp",
        "ArrowDown",
        "ArrowLeft",
        "ArrowRight",
        "Home",
        "End",
        "Escape",
        "Enter",
    }
)
FORBIDDEN = {
    "eval": re.compile(r"\beval\b"),
    "Function": re.compile(r"\bFunction\s*\("),
    "innerHTML": re.compile(r"\b(?:inner|outer)HTML\b"),
    "insertAdjacentHTML": re.compile(r"\binsertAdjacentHTML\b"),
    "document.write": re.compile(r"\bdocument\s*\.\s*write"),
    "localStorage": re.compile(r"\blocalStorage\b"),
    "sessionStorage": re.compile(r"\bsessionStorage\b"),
    "indexedDB": re.compile(r"\bindexedDB\b"),
    "caches": re.compile(r"\bcaches\b"),
    "cookie": re.compile(r"\bcookie\b"),
    "console": re.compile(r"\bconsole\b"),
    "on* handler": re.compile(r"\.\s*on[a-z]+\s*="),
    "style": re.compile(r"\.\s*style\b"),
    "setInterval": re.compile(r"\bsetInterval\b"),
    "requestAnimationFrame": re.compile(r"\brequestAnimationFrame\b"),
    "RegExp": re.compile(r"\bRegExp\b"),
    "a global set": re.compile(r"\b(?:window|globalThis|self|top|parent)\s*\.\s*\w+\s*=(?!=)"),
    "import()": re.compile(r"\bimport\s*\("),
    "postMessage": re.compile(r"\bpostMessage\b"),
    "window.open": re.compile(r"\bopen\s*\("),
    # Only fetch("<one literal>", ...) or fetch("<one literal>"): never a built URL.
    "fetch with a variable URL": re.compile(r'\bfetch\((?!\s*""\s*[,)])'),
}
PROSE_PROPERTY = re.compile(
    r"\.\s*(?:textContent|innerText|outerText|title|ariaLabel|alt|placeholder|value|label)"
    r"\s*=\s*$"
)
VISIBLE_ATTRIBUTES = frozenset(
    {"aria-label", "aria-description", "aria-roledescription", "title", "alt", "placeholder"}
)
SET_ATTRIBUTE_VALUE = re.compile(r'setAttribute\(\s*""\s*,\s*$')
STRICT_OPENER = re.compile(r"\A\s*(?:\(\s*(?:function\s*\(\s*\)|\(\s*\)\s*=>)\s*\{)?\s*")


def _scan(source: str) -> tuple[str, list[tuple[str, str]]]:
    """(code with every comment removed and every string replaced by "", and each string
    literal with the code just before it)."""
    code: list[str] = []
    strings: list[tuple[str, str]] = []
    i = 0
    while i < len(source):
        char = source[i]
        if source.startswith("//", i):
            end = source.find("\n", i)
            i = len(source) if end == -1 else end
        elif source.startswith("/*", i):
            end = source.find("*/", i + 2)
            assert end != -1, "unterminated comment"
            i = end + 2
        elif char in "\"'":
            j = i + 1
            while source[j] != char:
                assert source[j] != "\n", "unterminated string"
                j += 2 if source[j] == "\\" else 1
            strings.append((source[i + 1 : j], "".join(code)[-80:]))
            code.append('""')
            i = j + 1
        elif char == "`":
            raise AssertionError("template literals are not used in the panel's scripts")
        else:
            code.append(char)
            i += 1
    return "".join(code), strings


def _split(source: str) -> tuple[str, list[str]]:
    """(code with every comment removed and every string replaced by "", the string literals)."""
    code, strings = _scan(source)
    return code, [literal for literal, _ in strings]


def _source(name: str) -> str:
    return (STATIC_DIR / name).read_text("utf-8")


def _strict_first(source: str) -> bool:
    """Whether the first statement (inside an optional IIFE) is "use strict"."""
    code, strings = _split(source)
    return STRICT_OPENER.sub("", code).startswith('"";') and strings[:1] == ["use strict"]


TIMEOUT = re.compile(r"\bsetTimeout\b")


def _problems(source: str, timer_allowed: bool = False) -> list[str]:
    code, scanned = _scan(source)
    problems = [] if _strict_first(source) else ['"use strict" is not first']
    if not timer_allowed and TIMEOUT.search(code):
        problems.append("setTimeout")
    previous = ""
    for literal, before in scanned:
        # A non-empty literal written where a person reads it: assigned to a visible property,
        # or the value of setAttribute() for a visible attribute (whose name is the literal just
        # before). Even a single lowercase word there is prose.
        visible_attribute = previous in VISIBLE_ATTRIBUTES and SET_ATTRIBUTE_VALUE.search(before)
        if literal and (PROSE_PROPERTY.search(before) or visible_attribute):
            problems.append(f"prose position: {literal!r}")
        previous = literal
        if literal.lower().startswith(("javascript:", "data:", "vbscript:")):
            problems.append(f"javascript: or data: URL: {literal!r}")
        if literal not in ALLOWED_LITERALS and not PLAIN_TOKEN.fullmatch(literal):
            problems.append(f"prose or an unknown token: {literal!r}")
        if "\\" in literal:
            problems.append(f"an escape in a string: {literal!r}")
        if literal.startswith("//") or "://" in literal:
            problems.append(f"absolute URL: {literal!r}")
        if re.fullmatch(r"on[a-z]+", literal):
            problems.append(f"on* attribute: {literal!r}")
        if literal in ("style", "innerHTML", "outerHTML", "srcdoc"):
            problems.append(f"style or HTML by name: {literal!r}")
    for name, pattern in FORBIDDEN.items():
        if pattern.search(code):
            problems.append(name)
    return problems


@pytest.mark.parametrize("name", SCRIPTS)
def test_the_script_is_in_the_manifest(name: str) -> None:
    assert name in SERVED


@pytest.mark.parametrize("name", SCRIPTS)
def test_the_script_has_no_visible_text_and_nothing_forbidden(name: str) -> None:
    assert _problems(_source(name), timer_allowed=name == TIMER_SCRIPT) == []


def test_every_static_script_is_checked() -> None:
    assert {path.name for path in STATIC_DIR.glob("*.js")} == set(SCRIPTS)


def test_exactly_one_timer_in_all_the_scripts_and_it_is_in_session_js() -> None:
    timers = {name: _split(_source(name))[0].count("setTimeout(") for name in SCRIPTS}

    assert timers == {name: int(name == TIMER_SCRIPT) for name in SCRIPTS}
    assert sum(len(TIMEOUT.findall(_split(_source(name))[0])) for name in SCRIPTS) == 1


@pytest.mark.parametrize("name", SCRIPTS)
def test_each_script_fetches_only_its_own_literal_urls(name: str) -> None:
    _, strings = _scan(_source(name))
    fetched = {literal for literal, before in strings if before.endswith("fetch(")}

    assert fetched == FETCHES[name]


@pytest.mark.parametrize("name", SCRIPTS)
def test_each_script_is_a_standalone_iife(name: str) -> None:
    source = _source(name)

    assert source.startswith("(function () {\n")
    assert source.rstrip().endswith("}());")


def test_the_scripts_talk_through_the_activity_event() -> None:
    """panel.js and try.js tell session.js about every answer that was a use."""
    for name in ("panel.js", "try.js"):
        source = _source(name)
        assert f'new CustomEvent("{ACTIVITY_EVENT}")' in source, name
        assert "dispatchEvent(" in source, name
    assert f'addEventListener("{ACTIVITY_EVENT}"' in _source("session.js")


def test_panel_js_reports_every_answer_but_401_and_403() -> None:
    code, _ = _split(_source("panel.js"))

    assert "function activity(status)" in code
    assert "status !== 401 && status !== 403" in code
    assert code.count("activity(response.status)") >= 2  # the status view and the logout


@pytest.mark.parametrize("name", SCRIPTS)
def test_the_script_is_strict_ascii_without_bom_and_with_lf(name: str) -> None:
    data = (STATIC_DIR / name).read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf") and b"\r" not in data
    text = data.decode("ascii")  # no word of any language hides in the code
    assert _strict_first(text)


def test_the_login_never_sends_the_csrf_header_and_the_panel_does() -> None:
    assert "X-Antifaz-CSRF" not in _source("login.js")
    assert "X-Antifaz-CSRF" in _source("panel.js")
    assert "antifaz-csrf" in _source("panel.js")  # the token comes from the page's meta


def test_the_login_clears_the_field_before_it_waits() -> None:
    """The value is copied to a local and the field emptied before the first await or fetch."""
    code, _ = _split(_source("login.js"))
    cleared = code.find('.value = ""')
    assert cleared != -1
    for later in ("await", "fetch(", ".then("):
        position = code.find(later)
        assert position == -1 or position > cleared, later


@pytest.mark.parametrize(
    ("needle", "name"),
    [
        ('credentials: "same-origin"', "login.js"),
        ('cache: "no-store"', "login.js"),
        ('redirect: "error"', "login.js"),
        ('referrerPolicy: "no-referrer"', "login.js"),
        ('fetch("/panel/login"', "login.js"),
        ("location.replace(", "login.js"),
        ("textContent", "login.js"),
        ('credentials: "same-origin"', "panel.js"),
        ('cache: "no-store"', "panel.js"),
        ('redirect: "error"', "panel.js"),
        ('referrerPolicy: "no-referrer"', "panel.js"),
        ('fetch("/panel/logout"', "panel.js"),
        ("location.replace(", "panel.js"),
        ("pageshow", "panel.js"),
        ("persisted", "panel.js"),
        ("location.reload()", "panel.js"),
        ("textContent", "panel.js"),
        # The Status view (11c-3b): one literal URL, read only when shown, no timers.
        ('fetch("/panel/api/status"', "panel.js"),
        ('"visibilitychange"', "panel.js"),
        ('document.visibilityState === "visible"', "panel.js"),
        ('"aria-busy"', "panel.js"),
        ("Intl.DateTimeFormat(root.lang", "panel.js"),
        ('location.replace("/panel/login" + languageQuery())', "panel.js"),
        # «Prueba un texto» (11d-b): JSON with the CSRF token, never cached or redirected.
        *(
            (needle, script)
            for script in ("try.js", "session.js")
            for needle in (
                'credentials: "same-origin"',
                'cache: "no-store"',
                'redirect: "error"',
                'referrerPolicy: "no-referrer"',
                '"X-Antifaz-CSRF"',
                "meta[name=antifaz-csrf]",
                'method: "POST"',
            )
        ),
        ("isWellFormed", "try.js"),
        ('"Retry-After"', "try.js"),
        ('"text_too_long_for_ner"', "try.js"),
        ("length_utf16", "try.js"),
        ("names_checked", "try.js"),
        ('"pagehide"', "try.js"),
        ("JSON.stringify(", "try.js"),
        ('"visibilitychange"', "session.js"),
        ("meta[name=antifaz-session-idle]", "session.js"),
        ("meta[name=antifaz-session-max]", "session.js"),
        ('location.replace("/panel/login" + languageQuery())', "session.js"),
        ("clearTimeout(", "session.js"),
    ],
)
def test_the_fetch_options_and_navigation(needle: str, name: str) -> None:
    assert needle in _source(name)


def test_the_check_sees_what_it_forbids() -> None:
    bad = (
        '"use strict";\n'
        'el.innerHTML = "x"; eval("1"); new Function("a"); localStorage.x = 1;\n'
        "sessionStorage.clear(); indexedDB.open; document.cookie; console.log(1);\n"
        'fetch("https://example.invalid/"); el.onclick = f; el.style.color = "red";\n'
        'setTimeout(f, 1); b.textContent = "No se ha podido salir"; c.title = "Salir";\n'
        'fetch(url); document.write("x"); el.setAttribute("onclick", "x");\n'
        'el.setAttribute("style", "x"); fetch("//example.invalid/x");\n'
    )
    problems = _problems(bad)

    for expected in (
        "innerHTML",
        "eval",
        "Function",
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "cookie",
        "console",
        "absolute URL",
        "on* handler",
        "style",
        "style or HTML by name",
        "setTimeout",
        "'No se ha podido salir'",
        "'Salir'",
        "fetch with a variable URL",
        "document.write",
        "on* attribute",
    ):
        assert any(expected in problem for problem in problems), expected
    # Review of 11c-2b: bypasses that passed before.
    for sneaky, expected in (
        ('a.href = "javascript:alert(1)";', "javascript:"),
        ('el.textContent = "salir";', "prose position"),
        ('el.title = "cerrar";', "prose position"),
        ('el.setAttribute("aria-label", "cerrar");', "prose position"),
        ('el.setAttribute("title", "salir");', "prose position"),
        ('fetch("" + url);', "fetch with a variable URL"),
        ('fetch("/panel/" + name);', "fetch with a variable URL"),
        # 11d-b: the one timer is session.js's; no other kind of timer, regex or global.
        ("setInterval(f, 1000);", "setInterval"),
        ("requestAnimationFrame(f);", "requestAnimationFrame"),
        ('var r = new RegExp("[a-z]");', "RegExp"),
        ("window.antifaz = {};", "a global set"),
        ("globalThis.x = 1;", "a global set"),
    ):
        found = _problems('"use strict";\n' + sneaky + "\n")
        assert any(expected in problem for problem in found), sneaky
    for fine in (
        'el.textContent = "";',
        'fetch("/panel/login", {method: "POST"});',
        'el.setAttribute("aria-selected", "true");',
        'el.setAttribute("aria-label", other.getAttribute("data-text-dark"));',
        'window.addEventListener("pageshow", f);',
        "if (window.x === 1) {}",
    ):
        assert _problems('"use strict";\n' + fine + "\n") == [], fine
    assert _problems('"use strict";\nsetTimeout(f, 1);\n', timer_allowed=True) == []
    assert '"use strict" is not first' in _problems('var a = 1;\n"use strict";\n')
    assert _strict_first('(function () {\n  "use strict";\n}());')
    with pytest.raises(AssertionError):
        _split("var a = `x`;")
    assert _split('a = "b" /* "c" */ + \'d\'; // "e"\n') == ('a = ""  + ""; \n', ["b", "d"])
