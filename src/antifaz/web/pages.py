"""The panel's one fixed HTML page (ADR-0018): «Solo HTTPS». Standard library only.

The door answers it, with a 403, to plain HTTP from any host that is not localhost, before any
login, cookie or static file (the static files get it too). So it carries no styles and links
nothing: a plain page that says how to fix it. No <script>, no <style>, no style="..." and no
on...= attributes (the CSP blocks them all), and nothing the client sent.

The login and panel pages are Jinja2 templates since 11c-1 (templating.py).
"""

HTTPS_ONLY_PAGE = (
    "<!doctype html>\n"
    '<html lang="es">\n'
    "<head>\n"
    '<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    '<meta name="robots" content="noindex, nofollow">\n'
    "<title>Solo HTTPS · Antifaz</title>\n"
    "</head>\n"
    "<body>\n<main>\n"
    "<h1>El panel necesita HTTPS</h1>\n"
    "<p>Por HTTP, el panel solo funciona en <code>localhost</code>, "
    "<code>127.0.0.1</code> o <code>[::1]</code>.</p>\n"
    "<p>Para abrirlo desde otra dirección, ponlo detrás de un proxy inverso con HTTPS "
    "(Caddy, nginx o Traefik) que mantenga la cabecera <code>Host</code> original, y pon la "
    "IP del proxy en <code>ANTIFAZ_TRUSTED_PROXIES</code>.</p>\n"
    "</main>\n</body>\n</html>\n"
)
