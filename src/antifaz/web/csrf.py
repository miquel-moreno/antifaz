"""CSRF layer 3 of the panel and its JSON-only rule (ADR-0018). Standard library only.

A request that changes something must say, through headers only a browser sets, that it comes
from the panel's own page: exactly one `Sec-Fetch-Site: same-origin` and exactly one `Origin`
equal to "scheme://host", with the scheme the door decided and the Host the gateway already
checked. Its body must be JSON, which a form of another site cannot send without a preflight
(and there is no CORS, so the preflight fails).

Layers 1 (SameSite=Strict) and 2 (the X-Antifaz-CSRF token) come with the session routes of
11b-2b. The login has no session yet: it relies on this layer and on JSON.

Forbidden: comparing without the scheme or the port. Folding case or decoding the Origin.
"""

from collections.abc import Iterable

_JSON = "application/json"
_UTF8 = frozenset({"charset=utf-8", 'charset="utf-8"'})


def _values(headers: Iterable[tuple[bytes, bytes]], name: bytes) -> list[bytes]:
    return [value for key, value in headers if key.lower() == name]


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
