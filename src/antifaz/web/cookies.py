"""The panel's session cookie, by scheme (ADR-0018, addendum of 2026-10-13). Standard library only.

- HTTPS (direct, or X-Forwarded-Proto from a trusted proxy): `__Host-antifaz_session` with
  `Secure; HttpOnly; SameSite=Strict; Path=/`.
- http://localhost, 127.0.0.1 or [::1]: `antifaz_session` WITHOUT Secure (WebKit drops any
  Secure cookie over plain HTTP), with `HttpOnly; SameSite=Strict; Path=/panel`.
- Both last 8 hours (Max-Age) and never carry Domain.
- Logout expires the cookie of the request's scheme only (expired_cookie): the same name and
  attributes, an empty value and Max-Age=0. The session itself is bound to its scheme too
  (session_scheme(), sessions.py).

The server reads ONLY the name of the request's scheme. A cookie without Secure also travels over
HTTPS, so a cookie set by http://localhost must never count in an HTTPS session (and the other
way round). The Cookie header is parsed by hand: every Cookie line is joined (HTTP/2 may split
them), the name compares exactly and with case, and a name that comes more than once is no
session at all (the browser holds two, from two apps or two Antifaz on other localhost ports,
and either could be the attacker's).

Forbidden: logging a cookie value. Accepting the other scheme's name.
"""

import re
from collections.abc import Iterable

from antifaz.web.sessions import MAX_AGE_SECONDS, SessionScheme

SECURE_COOKIE = "__Host-antifaz_session"
LOCAL_COOKIE = "antifaz_session"
# What secrets.token_urlsafe() writes; anything else is not one of our ids.
_SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_OWS = " \t"


def cookie_name(scheme: str) -> str:
    """The cookie of this scheme: the __Host- one only over HTTPS."""
    return SECURE_COOKIE if scheme == "https" else LOCAL_COOKIE


def session_scheme(scheme: str) -> SessionScheme:
    """The scheme a session is bound to: "https" only over HTTPS, else "http-local"."""
    return "https" if scheme == "https" else "http-local"


def _attributes(scheme: str) -> str:
    if scheme == "https":
        return "Path=/; Secure; HttpOnly; SameSite=Strict"
    return "Path=/panel; HttpOnly; SameSite=Strict"


def session_cookie(scheme: str, session_id: str) -> str:
    """The Set-Cookie value that hands `session_id` to the browser over `scheme`."""
    return f"{cookie_name(scheme)}={session_id}; Max-Age={MAX_AGE_SECONDS}; {_attributes(scheme)}"


def expired_cookie(scheme: str) -> str:
    """The Set-Cookie value that makes the browser drop the session cookie of `scheme`."""
    return f"{cookie_name(scheme)}=; Max-Age=0; {_attributes(scheme)}"


def read_session_cookie(headers: Iterable[tuple[bytes, bytes]], scheme: str) -> str | None:
    """The session id in the cookie of `scheme`, or None: missing, repeated or not an id."""
    lines = [value.decode("latin-1") for name, value in headers if name.lower() == b"cookie"]
    wanted = cookie_name(scheme)
    found: list[str] = []
    for pair in "; ".join(lines).split(";"):
        name, equals, value = pair.strip(_OWS).partition("=")
        if equals and name == wanted:
            found.append(value)
    if len(found) != 1 or not _SESSION_ID.fullmatch(found[0]):
        return None
    return found[0]
