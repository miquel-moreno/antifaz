"""The panel's CSRF checks (ADR-0018). Standard library only. The door (door.py) applies them.

- Layer 1: SameSite=Strict on the session cookie (cookies.py).
- Layer 2, has_csrf_token(): exactly one `X-Antifaz-CSRF` header, ASCII and not empty, equal to
  the session's CSRF token (hmac.compare_digest on bytes). The page gets the token in a <meta>.
- Layer 3, is_same_origin(): a request that changes something must say, through headers only a
  browser sets, that it comes from the panel's own page: exactly one `Sec-Fetch-Site:
  same-origin` and exactly one `Origin` equal to "scheme://host", with the scheme the door
  decided and the Host the gateway already checked.
- is_json(): the body must be JSON, which a form of another site cannot send without a
  preflight (and there is no CORS, so the preflight fails).

The login has no session yet: it is the only route without layer 2 (door.CSRF_TOKEN_EXEMPT).

counts_as_use() is not a CSRF check: it says whether a request that passed the door comes from
the panel's own page (or from no browser at all), so that it may refresh the session's last use.

api_fetch_allowed() (11c-3b review): who may read /panel/api/*, even with GET. A browser always
sends Sec-Fetch-Site, so only `same-origin` (the panel's own fetch) passes; a page of another
site or another localhost port (same-site, which carries the cookie) and a navigation typed in
the address bar (`none`) are refused. Without the header it is not a browser (curl, a monitor)
and the session decides, as on the other routes.

Forbidden: comparing without the scheme or the port. Folding case or decoding the Origin.
Comparing the token with `==`. Logging a token.
"""

import hmac
from collections.abc import Iterable

CSRF_HEADER = b"x-antifaz-csrf"

_JSON = "application/json"
_UTF8 = frozenset({"charset=utf-8", 'charset="utf-8"'})


def _values(headers: Iterable[tuple[bytes, bytes]], name: bytes) -> list[bytes]:
    return [value for key, value in headers if key.lower() == name]


def has_csrf_token(headers: Iterable[tuple[bytes, bytes]], expected: str) -> bool:
    """Exactly one X-Antifaz-CSRF header, ASCII and not empty, equal to `expected`."""
    values = _values(headers, CSRF_HEADER)
    if len(values) != 1 or not values[0] or not values[0].isascii():
        return False
    if not expected or not expected.isascii():
        return False  # never a session's token; fail closed rather than raise
    return hmac.compare_digest(values[0], expected.encode("ascii"))


# Sec-Fetch-Site values of a request that may count as a use of the session: the panel's own
# page, or the user typing the address or using a bookmark. Absent: not a browser (curl).
_OWN_USE = frozenset({b"same-origin", b"none"})


def counts_as_use(headers: Iterable[tuple[bytes, bytes]]) -> bool:
    """No Sec-Fetch-Site, or exactly one that is same-origin or none. A page of another
    localhost port (same-site, which SameSite lets carry the cookie) never counts."""
    fetch_site = _values(headers, b"sec-fetch-site")
    return not fetch_site or (len(fetch_site) == 1 and fetch_site[0] in _OWN_USE)


def api_fetch_allowed(headers: Iterable[tuple[bytes, bytes]]) -> bool:
    """No Sec-Fetch-Site (not a browser), or exactly one that is same-origin."""
    fetch_site = _values(headers, b"sec-fetch-site")
    return not fetch_site or fetch_site == [b"same-origin"]


def is_same_origin(headers: Iterable[tuple[bytes, bytes]], scheme: str, host: str | None) -> bool:
    """One Sec-Fetch-Site: same-origin and one Origin that is exactly scheme://host."""
    headers = list(headers)
    if not host:
        return False
    fetch_site = _values(headers, b"sec-fetch-site")
    origin = _values(headers, b"origin")
    if fetch_site != [b"same-origin"] or len(origin) != 1:
        return False
    expected = f"{scheme}://{host}".encode("latin-1", "replace")
    return origin[0] == expected


def is_json(headers: Iterable[tuple[bytes, bytes]]) -> bool:
    """One Content-Type: application/json, optionally with charset=utf-8 and nothing else.

    The same rule as the gateway's (api/gate.py); written again here so that the panel's pure
    pieces stay standard library only."""
    values = _values(headers, b"content-type")
    if len(values) != 1:
        return False
    media, *params = values[0].decode("latin-1").split(";")
    if media.strip().lower() != _JSON:
        return False
    return all(param.strip().lower() in _UTF8 for param in params)
