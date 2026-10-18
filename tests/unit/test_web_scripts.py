"""The panel's two scripts, read as text (issue 53, PR 11c-2b): static/login.js and panel.js.

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

SCRIPTS = ("login.js", "panel.js")
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
    "setTimeout/setInterval": re.compile(r"\bset(?:Timeout|Interval)\b"),
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


def _problems(source: str) -> list[str]:
    code, scanned = _scan(source)
    problems = [] if _strict_first(source) else ['"use strict" is not first']
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
    assert _problems(_source(name)) == []


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
        "setTimeout/setInterval",
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
    ):
        found = _problems('"use strict";\n' + sneaky + "\n")
        assert any(expected in problem for problem in found), sneaky
    for fine in (
        'el.textContent = "";',
        'fetch("/panel/login", {method: "POST"});',
        'el.setAttribute("aria-selected", "true");',
        'el.setAttribute("aria-label", other.getAttribute("data-text-dark"));',
    ):
        assert _problems('"use strict";\n' + fine + "\n") == [], fine
    assert '"use strict" is not first' in _problems('var a = 1;\n"use strict";\n')
    assert _strict_first('(function () {\n  "use strict";\n}());')
    with pytest.raises(AssertionError):
        _split("var a = `x`;")
    assert _split('a = "b" /* "c" */ + \'d\'; // "e"\n') == ('a = ""  + ""; \n', ["b", "d"])
