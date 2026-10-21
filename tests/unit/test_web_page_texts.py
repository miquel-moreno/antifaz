"""Every word a panel page shows comes from a catalog (issue 53, PR 11c-2b).

The pages are rendered with "token catalogs": every text is replaced by ⟦its key⟧. Then every
text node and every attribute a person can see or hear (aria-label, title, alt, placeholder, the
value of a button, and the data-text-* attributes the scripts copy into the page) must be made
only of those tokens, white space and the middle dot. The only other things allowed: the version
(a server value), the shortcut digits 1-5 inside <kbd>, the pane numbers 1-3 of «Prueba un texto»
(11d-b, decoration), its word limit (a server value) and {placeholders} the scripts fill.

And the other way round: every key of en.json is used by a template (no dead keys).
"""

import re
from html.parser import HTMLParser
from types import MappingProxyType

import pytest

from antifaz import __version__
from antifaz.web.assets import load_assets
from antifaz.web.i18n import LANGUAGE_NAME, Catalog, Catalogs, load_catalogs, placeholders
from antifaz.web.sessions import MAX_AGE_SECONDS
from antifaz.web.templating import TEMPLATES, TEMPLATES_DIR, Pages

CATALOGS = load_catalogs()
ASSETS = load_assets()
TOKEN = re.compile(r"⟦[a-z0-9_.]+⟧")
LEFTOVER_OK = re.compile(r"[\s·]*")
PLACEHOLDER = re.compile(r"\{[a-z][a-z0-9_]*\}")
VISIBLE_ATTRIBUTES = frozenset(
    {"aria-label", "aria-description", "aria-roledescription", "title", "alt", "placeholder"}
)
TEXT_DATA = re.compile(r"data-text-[a-z-]+")


def _token_catalogs() -> Catalogs:
    """The real catalogs with each text replaced by ⟦key⟧ and its {placeholders} kept."""
    by_code = {}
    for catalog in CATALOGS:
        texts = {
            key: f"⟦{key}⟧" + "".join(f"{{{name}}}" for name in sorted(placeholders(text)))
            for key, text in catalog.texts.items()
        }
        by_code[catalog.code] = Catalog(catalog.code, MappingProxyType(texts))
    return Catalogs(MappingProxyType(by_code))


TOKEN_PAGES = Pages(ASSETS, version=__version__, catalogs=_token_catalogs())


class _Texts(HTMLParser):
    """Every visible text: (where, text)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[str, str]] = []
        self.stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value or "" for name, value in attrs}
        for name, value in values.items():
            if name in VISIBLE_ATTRIBUTES or TEXT_DATA.fullmatch(name):
                self.found.append((f"<{tag} {name}>", value))
        button_value = tag == "button" or values.get("type") in ("submit", "button", "reset")
        if button_value and "value" in values:
            self.found.append((f"<{tag} value>", values["value"]))
        if tag not in ("meta", "link", "input", "br", "img", "use", "path", "stop", "rect"):
            # 11d-b: the pane numbers of «Prueba un texto» (decoration, aria-hidden).
            numbered = tag == "span" and "num" in values.get("class", "").split()
            self.stack.append("num" if numbered else tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        names = [("span" if name == "num" else name) for name in self.stack]
        if tag in names:
            while ("span" if (name := self.stack.pop()) == "num" else name) != tag:
                pass

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.found.append((f"<{self.stack[-1] if self.stack else '?'}> text", data))


def _not_from_a_catalog(page: str, server_values: tuple[str, ...] = ()) -> list[str]:
    parser = _Texts()
    parser.feed(page)
    parser.close()
    problems = []
    for where, text in parser.found:
        if where == "<kbd> text" and re.fullmatch(r"[1-5]", text.strip()):
            continue
        if where == "<num> text" and re.fullmatch(r"[1-3]", text.strip()):
            continue
        rest = PLACEHOLDER.sub("", TOKEN.sub("", text)).replace(__version__, "")
        for value in server_values:
            rest = rest.replace(value, "")
        if not LEFTOVER_OK.fullmatch(rest):
            problems.append(f"{where}: {text!r}")
    return problems


NER_WORDS = 4321
# Distinct made-up limits, so each one is seen to come from the server.
LIVE_LIMITS = {"live_per_session": 7, "live_total": 9}
PAGES = [
    *((f"login-{code}", TOKEN_PAGES.login(code)) for code in CATALOGS.codes),
    # 11e-2: the limits of «En directo» in its busy text are server values too.
    *(
        (f"panel-{code}", TOKEN_PAGES.panel(code, "csrf-token-x", **LIVE_LIMITS))
        for code in CATALOGS.codes
    ),
    # 11d-b: with the NER on, the word counter and the too-long callout are in the page too; the
    # word limit in that callout is a server value (TRY_NER_MAX_WORDS), like the version.
    *(
        (
            f"panel-ner-{code}",
            TOKEN_PAGES.panel(code, "csrf-token-x", ner_words=NER_WORDS, **LIVE_LIMITS),
        )
        for code in CATALOGS.codes
    ),
]


@pytest.mark.parametrize(("name", "page"), PAGES, ids=[name for name, _ in PAGES])
def test_every_visible_text_comes_from_a_catalog(name: str, page: str) -> None:
    assert TOKEN.search(page)  # the token catalogs were really used
    values = (str(NER_WORDS), str(MAX_AGE_SECONDS // 3600), *map(str, LIVE_LIMITS.values()))
    assert _not_from_a_catalog(page, values) == []


def test_the_check_sees_a_text_that_is_not_from_a_catalog() -> None:
    bad = (
        "<p>⟦a⟧ Salir</p><button aria-label='Close'>⟦b⟧</button><img alt='logo'>"
        "<input type='submit' value='Send'><span data-text-x='Hola'></span>"
        "<span title='x'>⟦c⟧</span><kbd>9</kbd>"
    )
    problems = _not_from_a_catalog(bad)

    for expected in ("Salir", "Close", "logo", "Send", "Hola", "'x'", "'9'"):
        assert any(expected in problem for problem in problems), expected
    assert _not_from_a_catalog("<p>⟦a⟧ · <b>⟦b⟧</b></p><kbd>3</kbd><i>⟦v⟧{seconds}</i>") == []
    assert _not_from_a_catalog("<span class='num'>2</span><p>⟦a⟧12</p>", ("12",)) == []
    assert _not_from_a_catalog("<span class='num'>4</span><span>1</span><p>⟦a⟧12</p>") == [
        "<num> text: '4'",
        "<span> text: '1'",
        "<p> text: '⟦a⟧12'",
    ]


def test_every_catalog_key_is_used_by_a_template() -> None:
    used: set[str] = {LANGUAGE_NAME}  # each catalog's own name labels the selector
    for name in TEMPLATES:
        source = (TEMPLATES_DIR / name).read_text("utf-8")
        used |= set(re.findall(r"""\bt\(\s*["']([a-z0-9_.]+)["']""", source))
    reference = set(CATALOGS["en"].texts)

    assert used - reference == set()  # a template asks only for known keys
    assert reference - used == set()  # and no key is dead


def test_the_sidebar_says_what_happens_on_your_network() -> None:
    """Miquel's wording (review of the 11c-2b captures): not everything is processed on the
    user's network (the masked text does go to the AI), the personal data is."""
    assert CATALOGS["es"].text("sidebar.local") == (
        "Los datos personales se detectan y se cambian en tu red"
    )
    assert CATALOGS["en"].text("sidebar.local") == (
        "Personal data is detected and replaced on your network"
    )


# No visible text in the CSS either: a `content:` is empty, `none`, or copies a data-text-*
# attribute, which the page check above already requires to come from a catalog.
CSS_CONTENT = re.compile(r"(?<![\w-])content\s*:\s*([^;}]*)")
CONTENT_OK = re.compile(r'""|none|attr\(data-text-[a-z-]+\)')


def _content_values(css: str) -> list[str]:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    return [value.strip() for value in CSS_CONTENT.findall(css)]


def test_the_css_shows_no_text_of_its_own() -> None:
    for name, url in ASSETS.urls.items():
        if name.endswith(".css"):
            css = ASSETS.files[url.rsplit("/", 1)[1]].body.decode("utf-8")
            bad = [v for v in _content_values(css) if not CONTENT_OK.fullmatch(v)]
            assert bad == [], name


def test_the_css_content_check_sees_text() -> None:
    css = (
        '.a::before{content:"Salir"}.b::after{content:attr(title)}.c{align-content:center}'
        '.d::before{content:""}.e:focus{outline:0}.f::after{content:none}'
        ".g::before{content:attr(data-text-label)}"
    )
    values = _content_values(css)
    assert values == ['"Salir"', "attr(title)", '""', "none", "attr(data-text-label)"]
    assert [v for v in values if not CONTENT_OK.fullmatch(v)] == ['"Salir"', "attr(title)"]


# The login form lets password managers keep the token on purpose (autocomplete=current-password
# and a hidden username): no text may promise that the browser never keeps or saves it.
UNTRUE_PROMISES = re.compile(
    r"(browser|navegador)\b[^.]*\b(does not|doesn't|never|no)\b[^.]*\b(keep|save|store|guarda)",
    re.IGNORECASE,
)


@pytest.mark.parametrize("code", CATALOGS.codes)
def test_no_text_promises_the_browser_never_keeps_the_token(code: str) -> None:
    for key, text in CATALOGS[code].texts.items():
        assert not UNTRUE_PROMISES.search(text), (code, key)


def test_the_promise_check_sees_the_untrue_texts() -> None:
    for bad in (
        "The browser does not keep it.",
        "El navegador no lo guarda.",
        "Your browser never stores the token.",
    ):
        assert UNTRUE_PROMISES.search(bad), bad
    for fine in (
        "The panel stores nothing in your browser; your password manager may offer to save it.",
        "El panel no guarda nada en el navegador; tu gestor de contraseñas puede ofrecerse a "
        "guardarlo.",
    ):
        assert not UNTRUE_PROMISES.search(fine), fine
