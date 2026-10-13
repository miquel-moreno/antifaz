"""The panel's fixed HTML pages for 11b-2a (ADR-0018). Standard library only.

Placeholders until the interface of 11c: the login page, the page that asks for HTTPS and the
panel itself, which only carries the session's CSRF token in a <meta> for the scripts to come.
No <script>, no <style>, no style="..." and no on...= attributes: the CSP blocks them all.
Nothing the client sent is ever written into a page.
"""

import html

CSRF_META = "antifaz-csrf"

_HEAD = (
    "<!doctype html>\n"
    '<html lang="es">\n'
    "<head>\n"
    '<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    '<meta name="robots" content="noindex, nofollow">\n'
)


def _page(title: str, body: str, extra_head: str = "") -> str:
    return (
        f"{_HEAD}{extra_head}<title>{title} · Antifaz</title>\n</head>\n"
        f"<body>\n<main>\n{body}</main>\n</body>\n</html>\n"
    )


LOGIN_PAGE = _page(
    "Entrar",
    "<h1>Panel de Antifaz</h1>\n"
    "<p>La pantalla para entrar llega con la interfaz del panel (11c).</p>\n"
    "<p>Mientras tanto, se entra con <code>POST /panel/login</code> y el token de "
    "administrador (<code>ANTIFAZ_ADMIN_TOKEN</code>) en JSON.</p>\n",
)

HTTPS_ONLY_PAGE = _page(
    "Solo HTTPS",
    "<h1>El panel necesita HTTPS</h1>\n"
    "<p>Por HTTP, el panel solo funciona en <code>localhost</code>, "
    "<code>127.0.0.1</code> o <code>[::1]</code>.</p>\n"
    "<p>Para abrirlo desde otra dirección, ponlo detrás de un proxy inverso con HTTPS "
    "(Caddy, nginx o Traefik) que mantenga la cabecera <code>Host</code> original, y pon la "
    "IP del proxy en <code>ANTIFAZ_TRUSTED_PROXIES</code>.</p>\n",
)


def panel_page(csrf_token: str) -> str:
    """The panel for a live session: its CSRF token in the meta, and nowhere else."""
    meta = f'<meta name="{CSRF_META}" content="{html.escape(csrf_token, quote=True)}">\n'
    return _page(
        "Panel",
        "<h1>Panel de Antifaz</h1>\n"
        "<p>Has entrado. Las vistas del panel llegan con su interfaz (11c).</p>\n",
        extra_head=meta,
    )
