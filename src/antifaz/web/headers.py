"""The security headers of every /panel* answer (ADR-0018). Standard library only.

Everything closed by default, each kind of resource allowed one by one from the gateway itself:
no 'unsafe-inline', no 'unsafe-eval', no data:. No framing, no referrer, no sniffing, no shared
browsing context, no powerful features, and nothing kept in a cache. No CORS header, ever.
"""

from collections.abc import Iterable

CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "font-src 'self'; connect-src 'self'; manifest-src 'self'; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'"
)
_FEATURES_OFF = (
    "accelerometer",
    "autoplay",
    "camera",
    "display-capture",
    "geolocation",
    "gyroscope",
    "magnetometer",
    "microphone",
    "midi",
    "payment",
    "usb",
)
PERMISSIONS_POLICY = ", ".join(f"{feature}=()" for feature in _FEATURES_OFF)

SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("Content-Security-Policy", CONTENT_SECURITY_POLICY),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Content-Type-Options", "nosniff"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
    ("Permissions-Policy", PERMISSIONS_POLICY),
    ("Cache-Control", "no-store"),
)
_RAW = tuple(
    (name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in SECURITY_HEADERS
)
_NAMES = frozenset(name for name, _ in _RAW)


def with_security_headers(headers: Iterable[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    """ASGI response headers with ours set: a header of the same name is replaced, never kept."""
    return [(name, value) for name, value in headers if name.lower() not in _NAMES] + list(_RAW)
