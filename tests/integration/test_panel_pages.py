"""The login page and the panel page, per language, through the real door (issue 53, PR 11c-2b).

- GET /panel/login and GET /panel answer in the request's language: `?lang=xx` if it is a loaded
  catalog, else Accept-Language, else English. `<html lang>`, Content-Language and
  `Vary: Accept-Language` say which; the HTML stays `no-store`.
- Each page loads exactly one script, from the manifest (login.js or panel.js), and nothing the
  CSP would block (tests/html_rules.py).
- The login form posts nothing by itself: the token field has no name, the username field is a
  fixed "admin" for password managers, and login.js sends the JSON.
- The panel page: five tabs in the approved order (ARIA tabs), two «Salir» buttons (sidebar and
  phone bar), the CSRF token exactly once, and a language selector that only links.
- Red team: a ?lang or Accept-Language with markup is never reflected.
"""

import re
from collections.abc import Iterator
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient

from antifaz.web.assets import load_assets
from antifaz.web.headers import NO_STORE
from antifaz.web.i18n import load_catalogs
from tests.html_rules import html_problems
from tests.integration.panel_client import (
    LOCAL_URL,
    clients,
    cookie_of,
    log_in,
)

ASSETS = load_assets()
CATALOGS = load_catalogs()
TABS = ("playground", "live", "keys", "policies", "status")


@pytest.fixture(params=["https://antifaz.example", LOCAL_URL])
def client(request: pytest.FixtureRequest) -> Iterator[TestClient]:
    yield from clients(request.param)


class _Elements(HTMLParser):
    """Every start tag with its attributes, in order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))

    handle_startendtag = handle_starttag


def _elements(page: str) -> list[tuple[str, dict[str, str | None]]]:
    parser = _Elements()
    parser.feed(page)
    parser.close()
    return parser.tags


def _all(page: str, tag: str, **wanted: str) -> list[dict[str, str | None]]:
    """The tags with these attributes (`for_` is `for`, `data_x` is `data-x`; a boolean
    attribute has the value "")."""
    names = {key.rstrip("_").replace("_", "-"): value for key, value in wanted.items()}
    return [
        attrs
        for name, attrs in _elements(page)
        if name == tag and all(key in attrs and (attrs[key] or "") == v for key, v in names.items())
    ]


def _panel(client: TestClient, query: str = "", **headers: str) -> object:
    session = log_in(client)
    return client.get(
        "/panel" + query, headers={"Cookie": f"{cookie_of(client)}={session}", **headers}
    )


def _assert_language(response: object, lang: str) -> None:
    assert response.status_code == 200  # type: ignore[attr-defined]
    headers = response.headers  # type: ignore[attr-defined]
    page: str = response.text  # type: ignore[attr-defined]
    assert headers["content-language"] == lang
    assert headers["vary"] == "Accept-Language"
    assert headers["cache-control"] == NO_STORE
    assert headers["content-type"] == "text/html; charset=utf-8"
    assert f'<html lang="{lang}">' in page
    assert html_problems(page, ASSETS.paths) == []


# --- the language of the page -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "accept", "lang"),
    [
        ("", None, "en"),
        ("", "es-ES,es;q=0.9", "es"),
        ("", "fr-FR,fr;q=0.9", "en"),
        ("", "fr,es;q=0.5,en;q=0.1", "es"),
        ("?lang=es", "en", "es"),
        ("?lang=en", "es", "en"),
        ("?lang=fr", "es", "es"),
        ("?lang=xx", None, "en"),
    ],
)
def test_both_pages_answer_in_the_requested_language(
    client: TestClient, query: str, accept: str | None, lang: str
) -> None:
    headers = {"Accept-Language": accept} if accept else {}

    _assert_language(client.get("/panel/login" + query, headers=headers), lang)
    _assert_language(_panel(client, query, **headers), lang)


@pytest.mark.parametrize("code", CATALOGS.codes)
def test_each_language_shows_its_own_texts(client: TestClient, code: str) -> None:
    catalog = CATALOGS[code]
    login = client.get(f"/panel/login?lang={code}").text
    panel = _panel(client, f"?lang={code}").text  # type: ignore[attr-defined]

    assert catalog.text("login.heading") in login
    assert catalog.text("login.submit") in login
    for tab in TABS:
        assert catalog.text(f"tab.{tab}") in panel
    assert catalog.text("logout") in panel


# --- one script each, from the manifest -----------------------------------------------------------


def test_each_page_loads_exactly_its_scripts(client: TestClient) -> None:
    """The login one; the panel three (11d-b): panel.js, session.js and try.js, in that order."""
    login = client.get("/panel/login").text
    panel: str = _panel(client).text  # type: ignore[attr-defined]

    for page, names in ((login, ["login.js"]), (panel, ["panel.js", "session.js", "try.js"])):
        scripts = _all(page, "script")
        assert [script["src"] for script in scripts] == [ASSETS.urls[name] for name in names]
        assert all("defer" in script for script in scripts)
        assert page.count("<script") == len(names)


@pytest.mark.parametrize("name", ["login.js", "panel.js", "session.js", "try.js"])
def test_both_scripts_are_served_byte_for_byte_as_javascript(client: TestClient, name: str) -> None:
    from antifaz.web.assets import STATIC_DIR, STATIC_PREFIX

    response = client.get(ASSETS.urls[name])

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/javascript; charset=utf-8"
    assert response.content == (STATIC_DIR / name).read_bytes()
    assert ASSETS.urls[name].startswith(STATIC_PREFIX)


# --- the login form -------------------------------------------------------------------------------


def test_the_login_form_posts_nothing_by_itself(client: TestClient) -> None:
    page = client.get("/panel/login").text

    (form,) = _all(page, "form")
    assert form["method"] == "post"
    assert form["action"] == "/panel/login"
    assert "novalidate" in form
    inputs = _all(page, "input")
    (token,) = [attrs for attrs in inputs if attrs.get("type") == "password"]
    assert "name" not in token
    assert token["autocomplete"] == "current-password"
    assert token["spellcheck"] == "false"
    assert token["autocapitalize"] == "off"
    assert "value" not in token
    (user,) = [attrs for attrs in inputs if attrs.get("autocomplete") == "username"]
    assert user["type"] == "text"
    assert user["value"] == "admin"
    assert "name" not in user
    assert "hidden" in user
    assert len(inputs) == 2
    (alert,) = _all(page, "p", role="alert")
    assert alert["id"] == "login-alert"
    assert _all(page, "label", for_=str(token["id"]))


def test_the_login_page_says_nothing_of_a_session(client: TestClient) -> None:
    page = client.get("/panel/login").text

    assert "antifaz-csrf" not in page
    assert not _all(page, "button", data_logout="")


# --- the panel page -------------------------------------------------------------------------------


def test_the_panel_has_the_five_tabs_in_order(client: TestClient) -> None:
    page: str = _panel(client).text  # type: ignore[attr-defined]

    (tablist,) = _all(page, "div", role="tablist")
    assert tablist["aria-orientation"] == "vertical"
    tabs = _all(page, "button", role="tab")
    assert [tab["id"] for tab in tabs] == [f"tab-{name}" for name in TABS]
    assert [tab["aria-controls"] for tab in tabs] == [f"panel-{name}" for name in TABS]
    assert [tab["aria-selected"] for tab in tabs] == ["true"] + ["false"] * 4
    assert [tab["tabindex"] for tab in tabs] == ["0"] + ["-1"] * 4
    assert [tab["aria-keyshortcuts"] for tab in tabs] == ["1", "2", "3", "4", "5"]
    panels = _all(page, "section", role="tabpanel")
    assert [panel["id"] for panel in panels] == [f"panel-{name}" for name in TABS]
    assert [panel["aria-labelledby"] for panel in panels] == [f"tab-{name}" for name in TABS]
    assert ["hidden" in panel for panel in panels] == [False] + [True] * 4
    assert "#" not in "".join(str(tab.get("href", "")) for tab in tabs)  # no URL hash


def test_the_panel_has_two_logout_buttons_and_the_token_once(client: TestClient) -> None:
    session = log_in(client)
    page = client.get("/panel", headers={"Cookie": f"{cookie_of(client)}={session}"}).text
    (meta,) = _all(page, "meta", name="antifaz-csrf")
    token = str(meta["content"])

    buttons = _all(page, "button", data_logout="")
    assert [button["type"] for button in buttons] == ["button", "button"]
    assert len(token) >= 32
    assert page.count(token) == 1
    assert session not in page


class _Landmarks(HTMLParser):
    """Each <nav> with the number of <nav> elements around it."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.navs: list[int] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "nav":
            self.navs.append(self.depth)
            self.depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "nav":
            self.depth -= 1


def test_no_navigation_landmark_is_nested(client: TestClient) -> None:
    """The phone bar's language selector sits inside the sidebar's <nav>: it is a labelled
    group there, not a second landmark inside the first."""
    for page in (client.get("/panel/login").text, _panel(client).text):  # type: ignore[attr-defined]
        landmarks = _Landmarks()
        landmarks.feed(str(page))
        assert landmarks.navs and set(landmarks.navs) == {0}
    groups = _all(_panel(client).text, "div", role="group")  # type: ignore[attr-defined]
    # 11d-b: the other group is the column switcher of «Prueba un texto».
    assert [group["class"] for group in groups] == ["langs", "pswitch"]
    assert groups[0]["aria-label"] == CATALOGS["en"].text("language.label")


def test_the_language_selector_only_links(client: TestClient) -> None:
    login = client.get("/panel/login?lang=es").text
    panel: str = _panel(client, "?lang=es").text  # type: ignore[attr-defined]

    for page, path in ((login, "/panel/login"), (panel, "/panel")):
        links = [a for a in _all(page, "a") if a.get("hreflang")]
        expected = list(CATALOGS.codes)
        # the panel has the selector twice (header and phone bar), the login once
        assert [a["hreflang"] for a in links] == expected * (len(links) // len(expected))
        assert links
        for link in links:
            assert link["href"] == f"{path}?lang={link['hreflang']}"
            assert link["lang"] == link["hreflang"]
            if link["hreflang"] == "es":
                assert link["aria-current"] == "true"
            else:
                assert "aria-current" not in link
        names = re.findall(r'hreflang="[^"]+"[^>]*>([^<]+)</a>', page)
        assert set(names) == {catalog.name for catalog in CATALOGS}


# --- red team -------------------------------------------------------------------------------------

ODD_QUERIES = [
    "?lang=<script>alert(1)</script>",
    "?lang=%3Cscript%3E",
    '?lang=es"onmouseover="x',
    "?lang=es&lang=<b>",
    "?lang=" + "e" * 10_000,
    "?lang=../../etc/passwd",
    "?lang=es%00",
]
ODD_HEADERS = [
    "<script>alert(1)</script>",
    "es;q=<b>",
    '"><img src=x>',
    "x" * 50_000,
    ",".join(["fr"] * 5000) + ",es",
    "\t;;;==,,",
]


@pytest.mark.parametrize("query", ODD_QUERIES, ids=lambda value: value[:24])
def test_an_odd_lang_query_is_never_reflected(client: TestClient, query: str) -> None:
    for response in (client.get("/panel/login" + query), _panel(client, query)):
        page: str = response.text  # type: ignore[attr-defined]
        assert response.status_code == 200  # type: ignore[attr-defined]
        assert response.headers["content-language"] in CATALOGS.codes  # type: ignore[attr-defined]
        for marker in ("<script>alert", "lang=<b>", "onmouseover", "eeeeeeeeee", "passwd", "%3C"):
            assert marker not in page, marker
        assert html_problems(page, ASSETS.paths) == []


@pytest.mark.parametrize("header", ODD_HEADERS, ids=lambda value: value[:24])
def test_an_odd_accept_language_is_never_reflected(client: TestClient, header: str) -> None:
    for response in (
        client.get("/panel/login", headers={"Accept-Language": header}),
        _panel(client, **{"Accept-Language": header}),
    ):
        page: str = response.text  # type: ignore[attr-defined]
        assert response.status_code == 200  # type: ignore[attr-defined]
        assert response.headers["content-language"] == "en"  # type: ignore[attr-defined]
        for marker in ("<script>alert", "q=<b>", "<img src=x", "xxxxxxxxxx"):
            assert marker not in page, marker
        assert html_problems(page, ASSETS.paths) == []
