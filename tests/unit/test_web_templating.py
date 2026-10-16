"""The panel's templates (issue 53, ADR-0018, PR 11c-1): web/templating.py.

- jinja2.Environment with autoescape and StrictUndefined, no auto-reload, every template read
  and compiled once at startup (a DictLoader: serving a page never touches the disk).
- Templates only receive server values: the manifest's URLs and the session's CSRF token.
- Never Jinja2Templates nor request.url_for (they build URLs from the Host header): the static
  URLs are the fixed /panel/static/... of the manifest.
- The served HTML holds nothing the CSP would block (tests/html_rules.py, with html.parser).
- pages.py keeps only the fixed «Solo HTTPS» page, standard library and without styles.
"""

import ast
import re
from pathlib import Path

import jinja2
import pytest

import antifaz.web
from antifaz.web.assets import load_assets
from antifaz.web.pages import HTTPS_ONLY_PAGE
from antifaz.web.templating import CSRF_META, TEMPLATES, TEMPLATES_DIR, Pages, environment
from tests.html_rules import html_problems, html_urls

WEB = Path(antifaz.web.__file__).parent
ASSETS = load_assets()
PAGES = Pages(ASSETS)


def _copy_templates(tmp_path: Path, **changed: str) -> Path:
    for name in TEMPLATES:
        text = changed.get(name.removesuffix(".html"), (TEMPLATES_DIR / name).read_text("utf-8"))
        (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path


def test_the_templates_folder_holds_exactly_the_three_templates() -> None:
    assert TEMPLATES == ("base.html", "login.html", "panel.html")
    assert {path.name for path in TEMPLATES_DIR.iterdir()} == set(TEMPLATES)
    assert TEMPLATES_DIR == WEB / "templates"


def test_the_environment_escapes_fails_on_undefined_and_never_reloads() -> None:
    env = environment({"t.html": "{{ x }}"})

    assert env.autoescape is True
    assert env.undefined is jinja2.StrictUndefined
    assert env.auto_reload is False
    assert isinstance(env.loader, jinja2.DictLoader)
    with pytest.raises(jinja2.UndefinedError):
        env.get_template("t.html").render()


def test_autoescape_escapes_markup_in_a_value() -> None:
    page = PAGES.panel('<script>alert(1)</script>"&')

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;&#34;&amp;" in page


def test_the_panel_page_carries_the_csrf_token_in_its_meta_only() -> None:
    page = PAGES.panel("csrf-token-x")

    assert f'<meta name="{CSRF_META}" content="csrf-token-x">' in page
    assert page.count("csrf-token-x") == 1
    assert CSRF_META == "antifaz-csrf"


def test_the_login_page_is_rendered_once_and_has_no_csrf_meta() -> None:
    assert PAGES.login == PAGES.login
    assert CSRF_META not in PAGES.login
    assert "11c-2" in PAGES.login  # the real login screen comes with the next PR


@pytest.mark.parametrize("page", [PAGES.login, PAGES.panel("csrf-token-x"), HTTPS_ONLY_PAGE])
def test_served_pages_hold_nothing_the_csp_would_block(page: str) -> None:
    assert page.startswith("<!doctype html>")
    assert '<html lang="es">' in page
    assert '<meta charset="utf-8">' in page
    assert html_problems(page, ASSETS.paths) == []
    assert "http://" not in page and "https://" not in page


@pytest.mark.parametrize("page", [PAGES.login, PAGES.panel("csrf-token-x")])
def test_the_pages_load_the_panel_styles_and_favicon_from_the_manifest(page: str) -> None:
    urls = html_urls(page)
    for logical in ("fonts.css", "tokens.css", "panel.css", "logo.svg"):
        assert ASSETS.urls[logical] in urls, logical
    assert all(url in ASSETS.paths for url in urls if url.startswith("/panel/static/"))


def test_the_https_page_is_plain_offers_no_login_and_links_nothing() -> None:
    assert "HTTPS" in HTTPS_ONLY_PAGE and "localhost" in HTTPS_ONLY_PAGE
    for absent in ("<form", "<input", "<link", "<script", "/panel/static/"):
        assert absent not in HTTPS_ONLY_PAGE, absent


def test_pages_py_keeps_only_the_https_page() -> None:
    import antifaz.web.pages as pages

    public = {name for name in vars(pages) if not name.startswith("_") and name.isupper()}
    assert public == {"HTTPS_ONLY_PAGE"}


def test_the_html_rules_catch_what_they_forbid() -> None:
    bad = (
        "<style>a{}</style><script>x()</script><script src='/panel/static/x.js'>y</script>"
        '<a href="javascript:x">a</a><img src="data:image/png,x"><p style="x" onclick="y">p</p>'
        '<a href="https://example.invalid/">b</a><link href="/panel/static/nope.css">'
        '<base href="/"><meta http-equiv="refresh" content="0"><iframe src="#x"></iframe>'
    )

    problems = html_problems(bad, frozenset())

    for expected in (
        "<style>",
        "<script> without src",
        "code inside <script>",
        "javascript:",
        "data:",
        "style=",
        "onclick=",
        "is not a panel URL",
        "is not in the manifest",
        "<base>",
        "meta refresh",
        "<iframe>",
    ):
        assert any(expected in problem for problem in problems), expected


# --- What templates and the web code may never do ---------------------------------------------

UNSAFE_IN_TEMPLATES = re.compile(r"\|\s*safe\b|autoescape\s+false|Markup|\|\s*e\b\s*\(\s*false")


@pytest.mark.parametrize("name", TEMPLATES)
def test_no_template_turns_escaping_off(name: str) -> None:
    assert not UNSAFE_IN_TEMPLATES.search((TEMPLATES_DIR / name).read_text("utf-8"))


def test_the_unsafe_pattern_catches_what_it_forbids() -> None:
    for bad in ("{{ x|safe }}", "{{ x | safe }}", "{% autoescape false %}", "{{ Markup(x) }}"):
        assert UNSAFE_IN_TEMPLATES.search(bad), bad


def test_the_web_code_never_builds_urls_from_the_request() -> None:
    forbidden = {"Jinja2Templates", "TemplateResponse", "url_for", "base_url", "Markup"}
    for path in sorted(WEB.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(node, ast.Attribute):
                assert node.attr not in forbidden, (path.name, node.attr)
            elif isinstance(node, ast.Name):
                assert node.id not in forbidden, (path.name, node.id)
            elif isinstance(node, ast.alias):
                assert node.name.rsplit(".", 1)[-1] not in forbidden, (path.name, node.name)


def test_a_template_with_an_undefined_value_stops_the_startup(tmp_path: Path) -> None:
    body = "{% block body %}{{ nothing }}{% endblock %}"
    folder = _copy_templates(tmp_path, panel='{% extends "base.html" %}' + body)

    with pytest.raises(jinja2.UndefinedError):
        Pages(ASSETS, folder)


def test_a_template_asking_for_a_static_file_outside_the_manifest_stops_the_startup(
    tmp_path: Path,
) -> None:
    folder = _copy_templates(tmp_path, login="{{ static['missing.css'] }}")

    with pytest.raises(jinja2.UndefinedError):
        Pages(ASSETS, folder)


def test_rendering_never_touches_the_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = Pages(ASSETS)

    def no_disk(*args: object, **kwargs: object) -> object:
        raise AssertionError("the disk was read while serving")

    monkeypatch.setattr(Path, "read_text", no_disk)
    monkeypatch.setattr(Path, "read_bytes", no_disk)
    monkeypatch.setattr(Path, "open", no_disk)
    monkeypatch.setattr("builtins.open", no_disk)

    assert "csrf-1" in pages.panel("csrf-1")
    assert pages.login
