"""The panel's HTML pages from Jinja2 templates (ADR-0018, PRs 11c-1 and 11c-2b).

One jinja2.Environment with autoescape on and StrictUndefined (a missing value is an error,
never an empty string), without auto-reload. Every template is read once at startup into a
DictLoader and compiled there, and each page is rendered once per language to check it: a broken
template, a key missing from a catalog or a static file missing from the manifest stops the
gateway from starting, and serving a page never touches the disk.

One page per language (11c-2b): the login page is rendered completely at startup, once per
loaded catalog; the panel page is compiled once and rendered per request with the catalog of
the request's language and the session's CSRF token. Every visible word comes from the catalog,
through `t("key")` (tests/unit/test_web_page_texts.py).

Templates only ever receive server values: the manifest's URLs (`static`, logical name -> fixed
/panel/static/<hashed name>), the texts of the loaded catalogs and their codes, the version,
the page's own fixed path (`here`, for the language links) and, for the panel page, the
session's CSRF token. Never something the client sent: the language is always one of the loaded
codes, never the raw ?lang or Accept-Language. Never Jinja2Templates nor request.url_for: they
build URLs from the Host header. What the user types never goes through a template (it travels
as JSON and the page writes it with textContent).

Forbidden: |safe, {% autoescape false %} or Markup in a template (a test reads them).
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import jinja2

from antifaz.web.assets import Assets
from antifaz.web.i18n import Catalogs, load_catalogs
from antifaz.web.live_limits import MAX_PER_SESSION, MAX_STREAMS
from antifaz.web.sessions import MAX_AGE_SECONDS

TEMPLATES_DIR = Path(__file__).parent / "templates"
TEMPLATES = ("base.html", "parts.html", "login.html", "panel.html")
CSRF_META = "antifaz-csrf"
LOGIN_PATH = "/panel/login"
PANEL_PATH = "/panel"


def environment(sources: Mapping[str, str]) -> jinja2.Environment:
    """The only way the panel builds a Jinja2 environment: escaping on, strict, from memory."""
    return jinja2.Environment(
        loader=jinja2.DictLoader(dict(sources)),
        autoescape=True,
        undefined=jinja2.StrictUndefined,
        auto_reload=False,
    )


class Pages:
    """The rendered login page of each language and the panel page's compiled template."""

    def __init__(
        self,
        assets: Assets,
        *,
        version: str,
        catalogs: Catalogs | None = None,
        folder: Path = TEMPLATES_DIR,
    ) -> None:
        sources = {name: (folder / name).read_text(encoding="utf-8") for name in TEMPLATES}
        env = environment(sources)
        self.catalogs = load_catalogs() if catalogs is None else catalogs
        self._static = dict(assets.urls)
        self._version = version
        self._languages = tuple((catalog.code, catalog.name) for catalog in self.catalogs)
        login = env.get_template("login.html")
        self._login = {
            code: login.render(**self._context(code, LOGIN_PATH)) for code in self.catalogs.codes
        }
        self._panel = env.get_template("panel.html")
        for code in self.catalogs.codes:  # rendered at startup: a missing value stops it here
            for words in (None, 1):  # the NER off and on
                self.panel(
                    code,
                    "",
                    idle_seconds=0,
                    max_seconds=0,
                    ner_words=words,
                    max_text_bytes=1,
                    live_per_session=1,
                    live_total=1,
                )

    @property
    def languages(self) -> tuple[str, ...]:
        """The codes of the loaded catalogs, the reference first."""
        return self.catalogs.codes

    def _context(self, lang: str, here: str) -> dict[str, Any]:
        return {
            "static": self._static,
            "t": self.catalogs[lang].text,
            "lang": lang,
            "languages": self._languages,
            "here": here,
            "version": self._version,
        }

    def login(self, lang: str) -> str:
        """The login page in `lang` (one of `languages`), rendered at startup."""
        return self._login[lang]

    def panel(
        self,
        lang: str,
        csrf_token: str,
        *,
        idle_seconds: int = 0,
        max_seconds: int = 0,
        ner_words: int | None = None,
        max_text_bytes: int = 0,
        live_per_session: int = MAX_PER_SESSION,
        live_total: int = MAX_STREAMS,
    ) -> str:
        """The panel in `lang` for a live session: its CSRF token in the meta, and nowhere else;
        the seconds it has left (11d-b, session.js), the words a Try may give the name detector
        (None: the NER is off), the bytes a Try text may have and (11e-2) the limits of «En
        directo» streams per session and in total (web/live_stream.py, for its busy text),
        all server values."""
        return self._panel.render(
            **self._context(lang, PANEL_PATH),
            csrf_meta=CSRF_META,
            csrf_token=csrf_token,
            idle_seconds=idle_seconds,
            max_seconds=max_seconds,
            ner_words=ner_words,
            max_text_bytes=max_text_bytes,
            session_hours=MAX_AGE_SECONDS // 3600,
            live_per_session=live_per_session,
            live_total=live_total,
        )
