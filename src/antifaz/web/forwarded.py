"""Who is asking, and over what, for the panel's door only (ADR-0018). Standard library only.

The gateway never reads X-Forwarded-*: uvicorn runs with proxy headers off. The panel reads them
here, and only when the connection comes from a proxy in ANTIFAZ_TRUSTED_PROXIES:

- client_ip(): the address the login attempt limit counts. X-Forwarded-For is walked from RIGHT
  to LEFT (each proxy appends the address it saw) and the first address that is not a trusted
  proxy is the client. The leftmost entry is never taken by default: the client writes it.
  Entries left of that client are never even parsed, so a client cannot make the result fall
  back to the proxy's own address by prepending garbage.
- effective_scheme(): X-Forwarded-Proto, exactly one header holding exactly "https" or "http".
  Anything else (repeated, a list, other case, garbage) fails closed: not https.
- is_local_host(): the panel works over plain HTTP only on localhost, decided by the Host header
  (already checked by the gateway), never by the peer address (ADR-0018).

Forbidden: taking the leftmost X-Forwarded-For entry. Reading X-Forwarded-* from a peer that is
not a trusted proxy. Raising on a malformed header (it is the client's input: fall back).
"""

import ipaddress
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

type IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
type IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
type Scope = Mapping[str, Any]

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]"})
# Characters written around list elements in a header (RFC 9110 OWS).
_OWS = " \t"
_MAX_PORT_DIGITS = 5


def parse_networks(entries: Iterable[str]) -> tuple[IPNetwork, ...]:
    """ANTIFAZ_TRUSTED_PROXIES as networks: an IP is a /32 (or /128) network.

    Raises ValueError, with a fixed message, for garbage, host bits (10.0.0.1/8) or a /0 range.
    check_safe_to_start refuses those first, so the gateway never starts with one.
    """
    networks: list[IPNetwork] = []
    for entry in entries:
        try:
            if "%" in entry:  # a zone id: a peer address never carries one
                raise ValueError
            network = ipaddress.ip_network(entry.strip(), strict=True)
        except ValueError:
            raise ValueError("not an IP address or a CIDR range without host bits") from None
        if network.prefixlen == 0:
            raise ValueError("a /0 range would trust every client")
        networks.append(network)
    return tuple(networks)


def _address(text: str) -> IPAddress | None:
    """One bare IP address, IPv4-mapped IPv6 as IPv4; None for anything else.

    No port ("1.2.3.4:80"), no brackets ("[::1]"), no zone id ("fe80::1%eth0"), no "unknown"
    or obfuscated identifier (RFC 7239), no spaces inside."""
    if not text or "%" in text:
        return None
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _is_trusted(address: IPAddress, trusted: Sequence[IPNetwork]) -> bool:
    return any(address in network for network in trusted)  # other IP version: False


def _headers(scope: Scope, name: bytes) -> list[str]:
    """Every value of header `name` (lower case), in order, decoded as latin-1 like ASGI."""
    return [
        value.decode("latin-1") for key, value in scope.get("headers", ()) if key.lower() == name
    ]


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

    Repeated X-Forwarded-For headers are read as one list, in order. If every entry walked is a
    trusted proxy, or the header is missing or empty, or an entry walked is not a bare IP, the
    peer address is used.
    """
    peer = peer_ip(scope)
    if peer is None or not _is_trusted(peer, trusted):
        return _peer_text(scope)
    values = _headers(scope, b"x-forwarded-for")
    if not values:
        return str(peer)
    entries = ",".join(values).split(",")
    for entry in reversed(entries):
        address = _address(entry.strip(_OWS))
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


def _without_port(host: str) -> str | None:
    """`host` without ":port"; None if the port is not 1-5 digits or the brackets do not close."""
    if host.startswith("["):
        end = host.find("]")
        if end == -1:
            return None
        name, rest = host[: end + 1], host[end + 1 :]
    else:
        name, colon, port = host.partition(":")
        rest = colon + port
    if rest:
        port = rest[1:]
        if not rest.startswith(":") or not (port.isascii() and port.isdigit()):
            return None
        if len(port) > _MAX_PORT_DIGITS:
            return None
    return name


def is_local_host(host: str | None) -> bool:
    """True only for localhost, 127.0.0.1 or [::1], with or without a port.

    "localhost.evil.com", "127.0.0.2", "localhost." and "::1" without brackets are not local."""
    if not host:
        return False
    name = _without_port(host.lower())
    return name in LOCAL_HOSTS
