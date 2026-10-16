"""The security headers of every /panel* answer (ADR-0018). Standard library only.

Everything closed by default, each kind of resource allowed one by one from the gateway itself:
no 'unsafe-inline', no 'unsafe-eval', no data:. No framing, no referrer, no sniffing, no shared
browsing context, no powerful features. No CORS header, ever.

Cache-Control is decided per answer by the door (door.py), never by a route: NO_STORE for every
answer (pages, the panel's API, every error), and IMMUTABLE only for a 200 of a static file of
the manifest (hashed name: a new version is a new URL). SECURITY_HEADERS lists the default.
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
    "bluetooth",
    "camera",
    "display-capture",
    "geolocation",
    "gyroscope",
    "hid",
    "magnetometer",
    "microphone",
    "midi",
    "payment",
    "publickey-credentials-get",
    "screen-wake-lock",
    "serial",
    "usb",
    "xr-spatial-tracking",
)
PERMISSIONS_POLICY = ", ".join(f"{feature}=()" for feature in _FEATURES_OFF)
NO_STORE = "no-store"
IMMUTABLE = "public, max-age=31536000, immutable"
_CACHE_VALUES = frozenset({NO_STORE, IMMUTABLE})

SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("Content-Security-Policy", CONTENT_SECURITY_POLICY),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Content-Type-Options", "nosniff"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
    ("Permissions-Policy", PERMISSIONS_POLICY),
    ("Cache-Control", NO_STORE),  # the default; IMMUTABLE only where the door says so
)
_RAW = tuple(
    (name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in SECURITY_HEADERS
)
_NAMES = frozenset(name for name, _ in _RAW)


def with_security_headers(
    headers: Iterable[tuple[bytes, bytes]], cache_control: str = NO_STORE
) -> list[tuple[bytes, bytes]]:
    """ASGI response headers with ours set: a header of the same name is replaced, never kept.
    `cache_control` is NO_STORE or IMMUTABLE, nothing else."""
    if cache_control not in _CACHE_VALUES:
        raise ValueError("unknown Cache-Control for the panel")
    ours = [
        (name, cache_control.encode("latin-1") if name == b"cache-control" else value)
        for name, value in _RAW
    ]
    return [(name, value) for name, value in headers if name.lower() not in _NAMES] + ours
