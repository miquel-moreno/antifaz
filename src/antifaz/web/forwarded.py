"""Who is asking, and over what, for the panel's door only (ADR-0018). Standard library only.

The gateway never reads X-Forwarded-*: uvicorn runs with proxy headers off. The panel reads them
here, and only when the connection comes from a proxy in ANTIFAZ_TRUSTED_PROXIES:

- client_ip(): the address the login attempt limit counts. X-Forwarded-For is walked from RIGHT
  to LEFT (each proxy appends the address it saw) and the first address that is not a trusted
  proxy is the client. The leftmost entry is never taken by default: the client writes it.
  Entries left of that client are never even parsed, so a client cannot make the result fall
  back to the proxy's own address by prepending garbage.
  - Bounded: at most MAX_HOPS entries and the rightmost MAX_FORWARDED_BYTES of the header text
    are looked at, working from the right (a huge or endlessly repeated header costs nothing
    more). Text cut off on the left is simply not read; the cut alone never makes the result
    fall back to the peer.
  - Empty list elements are skipped (RFC 9110 §5.6.1). An entry may carry a port, as some
    proxies write it: "a.b.c.d:port" and "[v6]:port" count as their address. "unknown",
    obfuscated identifiers (RFC 7239), zone ids and junk fall back to the peer.
  - Repeated X-Forwarded-For lines are read in order, as one list (RFC 9110 §5.3). A proxy that
    PREPENDS its own line instead of appending to the last one leaves the client's line on the
    right: the rightmost entry is then written by the client. Trust only proxies that append.
- effective_scheme(): X-Forwarded-Proto, exactly one header holding exactly "https" or "http".
  Anything else (repeated, a list, other case, garbage) fails closed: not https.
- is_local_host(): the panel works over plain HTTP only on localhost, decided by the Host header
  (already checked by the gateway), never by the peer address (ADR-0018).

Forbidden: taking the leftmost X-Forwarded-For entry. Reading X-Forwarded-* from a peer that is
not a trusted proxy. Raising on a malformed header (it is the client's input: fall back).
"""

import ipaddress
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import Any

from antifaz.trusted_networks import IPNetwork, parse_proxy_network

type IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
type Scope = Mapping[str, Any]

__all__ = [
    "LOCAL_HOSTS",
    "MAX_FORWARDED_BYTES",
    "MAX_HOPS",
    "IPAddress",
    "IPNetwork",
    "client_ip",
    "effective_scheme",
    "is_local_host",
    "parse_networks",
    "peer_ip",
    "request_host",
]

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]"})
MAX_HOPS = 16
MAX_FORWARDED_BYTES = 8 * 1024
# Characters written around list elements in a header (RFC 9110 OWS).
_OWS = " \t"
_MAX_PORT = 65535
_MAX_PORT_DIGITS = 5
_XFF = b"x-forwarded-for"


def parse_networks(entries: Iterable[str]) -> tuple[IPNetwork, ...]:
    """ANTIFAZ_TRUSTED_PROXIES as networks (antifaz.trusted_networks: the same rule as the
    settings check). Raises ValueError with a fixed message; check_safe_to_start refuses those
    entries first, so the gateway never starts with one."""
    return tuple(parse_proxy_network(entry) for entry in entries)


def _valid_port(port: str) -> bool:
    """1 to 65535, written with ASCII digits only."""
    return (
        port.isascii()
        and port.isdigit()
        and len(port) <= _MAX_PORT_DIGITS
        and 1 <= int(port) <= _MAX_PORT
    )


def _split_port(text: str) -> str | None:
    """`text` without its ":port" ("[v6]" keeps its brackets); None if the port or the brackets
    are wrong. A bare IPv6 address has more than one ":" and comes back with an empty name."""
    if text.startswith("["):
        end = text.find("]")
        if end == -1:
            return None
        name, rest = text[: end + 1], text[end + 1 :]
    else:
        name, colon, port = text.partition(":")
        rest = colon + port
    if rest and not (rest.startswith(":") and _valid_port(rest[1:])):
        return None
    return name


def _address(text: str) -> IPAddress | None:
    """One bare IP address, IPv4-mapped IPv6 as IPv4; None for anything else (no zone id)."""
    if not text or "%" in text:
        return None
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _entry_address(entry: str) -> IPAddress | None:
    """An X-Forwarded-For entry: a bare address, "a.b.c.d:port", "[v6]" or "[v6]:port"."""
    if entry.count(":") > 1 and not entry.startswith("["):
        return _address(entry)  # a bare IPv6 address
    name = _split_port(entry)
    if name is None:
        return None
    if name.startswith("["):
        inside = name[1:-1]  # _split_port keeps "[...]" whole
        return _address(inside) if ":" in inside else None  # "[1.2.3.4]" is not a form
    return _address(name)


def _is_trusted(address: IPAddress, trusted: Sequence[IPNetwork]) -> bool:
    return any(address in network for network in trusted)  # other IP version: False


def _headers(scope: Scope, name: bytes) -> list[str]:
    """Every value of header `name` (lower case), in order, decoded as latin-1 like ASGI."""
    return [
        value.decode("latin-1") for key, value in scope.get("headers", ()) if key.lower() == name
    ]


def _rightmost_forwarded(scope: Scope) -> Iterator[str]:
    """The non-empty X-Forwarded-For entries, RIGHTMOST FIRST and lazily: at most MAX_HOPS of
    them, from at most the last MAX_FORWARDED_BYTES of the header text. Lines are read from the
    last one, and only as far as the caller asks."""
    headers = scope.get("headers", ())
    if not isinstance(headers, Sequence):
        headers = list(headers)
    walked = 0
    budget = MAX_FORWARDED_BYTES
    for key, value in reversed(headers):
        if key.lower() != _XFF:
            continue
        cut = len(value) > budget
        piece = value[-budget:] if cut else value
        parts = piece.decode("latin-1").split(",")
        if cut:
            parts = parts[1:]  # the leftmost part may be half an entry: not read
        for part in reversed(parts):
            entry = part.strip(_OWS)
            if entry:  # empty list elements do not count (RFC 9110 §5.6.1)
                yield entry
                walked += 1
                if walked == MAX_HOPS:
                    return
        budget -= len(piece) + 1  # and the "," that joins two lines
        if budget <= 0:
            return


def peer_ip(scope: Scope) -> IPAddress | None:
    """The address of the connection itself, or None (no client, or not an IP: "testclient")."""
    client = scope.get("client")
    if not client:
        return None
    host = client[0]
    return _address(host) if isinstance(host, str) else None


def _peer_text(scope: Scope) -> str:
    """The peer as the limiter counts it: its address, else whatever the server gave, else
    "unknown" (one bucket for every request without a client address)."""
    address = peer_ip(scope)
    if address is not None:
        return str(address)
    client = scope.get("client")
    host = client[0] if client else None
    return host if isinstance(host, str) and host else "unknown"


def client_ip(scope: Scope, trusted: Sequence[IPNetwork]) -> str:
    """The client's address for the login attempt limit (see the module docstring).

    If every entry looked at is a trusted proxy, or there is no entry, or the first entry that
    is not a trusted proxy is not an address, the peer address is used.
    """
    peer = peer_ip(scope)
    if peer is None or not _is_trusted(peer, trusted):
        return _peer_text(scope)
    for entry in _rightmost_forwarded(scope):
        address = _entry_address(entry)
        if address is None:
            return str(peer)  # malformed where only trusted proxies write: do not guess
        if not _is_trusted(address, trusted):
            return str(address)
    return str(peer)


def effective_scheme(scope: Scope, trusted: Sequence[IPNetwork]) -> str:
    """The scheme the browser used: scope["scheme"], or X-Forwarded-Proto from a trusted proxy.

    From a trusted proxy, exactly one header with exactly "https" or "http" counts; anything
    else gives "http", so the panel never believes HTTPS that was not checked.
    """
    scheme = str(scope.get("scheme", "http"))
    peer = peer_ip(scope)
    if peer is None or not _is_trusted(peer, trusted):
        return scheme
    values = _headers(scope, b"x-forwarded-proto")
    if not values:
        return scheme
    if len(values) == 1 and values[0] in ("https", "http"):
        return values[0]
    return "http"


def request_host(scope: Scope) -> str | None:
    """The Host header in lower case, or None if it is missing or repeated."""
    values = _headers(scope, b"host")
    return values[0].lower() if len(values) == 1 else None


def is_local_host(host: str | None) -> bool:
    """True only for localhost, 127.0.0.1 or [::1], with or without a port (1-65535).

    "localhost.evil.com", "127.0.0.2", "localhost." and "::1" without brackets are not local."""
    if not host:
        return False
    return _split_port(host.lower()) in LOCAL_HOSTS
