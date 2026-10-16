"""The panel's HTML pages from Jinja2 templates (ADR-0018, PR 11c-1).

One jinja2.Environment with autoescape on and StrictUndefined (a missing value is an error,
never an empty string), without auto-reload. Every template is read once at startup into a
DictLoader and compiled there, and each page is rendered once to check it: a broken template or
a static file missing from the manifest stops the gateway from starting, and serving a page
never touches the disk.

Templates only ever receive server values: the manifest's URLs (`static`, logical name -> fixed
/panel/static/<hashed name>) and, for the panel page, the session's CSRF token. Never something
the client sent. Never Jinja2Templates nor request.url_for: they build URLs from the Host
header. What the user types never goes through a template (it travels as JSON and the page
writes it with textContent).

Forbidden: |safe, {% autoescape false %} or Markup in a template (a test reads them).
"""

from collections.abc import Mapping
from pathlib import Path

import jinja2

from antifaz.web.assets import Assets

TEMPLATES_DIR = Path(__file__).parent / "templates"
TEMPLATES = ("base.html", "login.html", "panel.html")
CSRF_META = "antifaz-csrf"


def environment(sources: Mapping[str, str]) -> jinja2.Environment:
    """The only way the panel builds a Jinja2 environment: escaping on, strict, from memory."""
    return jinja2.Environment(
        loader=jinja2.DictLoader(dict(sources)),
        autoescape=True,
        undefined=jinja2.StrictUndefined,
        auto_reload=False,
    )


class Pages:
    """The rendered login page and the panel page's compiled template, over one manifest."""

    def __init__(self, assets: Assets, folder: Path = TEMPLATES_DIR) -> None:
        sources = {name: (folder / name).read_text(encoding="utf-8") for name in TEMPLATES}
        env = environment(sources)
        self._static = dict(assets.urls)
        self._login = env.get_template("login.html").render(static=self._static)
        self._panel = env.get_template("panel.html")
        self.panel("")  # rendered once at startup: a missing value stops it here

    @property
    def login(self) -> str:
        return self._login

    def panel(self, csrf_token: str) -> str:
        """The panel for a live session: its CSRF token in the meta, and nowhere else."""
        return self._panel.render(static=self._static, csrf_meta=CSRF_META, csrf_token=csrf_token)
