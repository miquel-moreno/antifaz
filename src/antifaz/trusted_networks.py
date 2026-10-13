"""One rule for ANTIFAZ_TRUSTED_PROXIES (ADR-0018), shared by config.py and the panel's door.

Standard library only, and outside antifaz.web: the settings check uses it on every start, and
nothing of the panel is loaded while the panel is off.

An entry is one IP address (a /32 or /128 network) or a CIDR range without host bits. Refused:
garbage, host bits (10.0.0.1/8), a zone id (fe80::1%eth0), 0.0.0.0/0 and ::/0 (they would
trust every client), and IPv4-mapped IPv6 ranges (::ffff:...): peer addresses are read as IPv4,
so such a range would never match and the proxy would silently not be trusted.

Messages are fixed text: they never repeat the entry.
"""

import ipaddress

type IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

# Wider than this, a trusted range probably holds more than the proxy (doctor warns).
NARROW_IPV4_PREFIX = 24
NARROW_IPV6_PREFIX = 64
_MAPPED = ipaddress.IPv6Network("::ffff:0:0/96")

GARBAGE = "only accepts IP addresses or CIDR ranges without host bits (10.0.0.0/8, not 10.0.0.1/8)"
EVERYTHING = "must not hold 0.0.0.0/0 or ::/0: it would trust every client"
MAPPED = "must not hold IPv4-mapped IPv6 (::ffff:...): write the IPv4 address or range"


def parse_proxy_network(entry: str) -> IPNetwork:
    """The network of one entry. Raises ValueError with one of the fixed messages above."""
    if "%" in entry:  # a zone id: a peer address never carries one
        raise ValueError(GARBAGE)
    try:
        network = ipaddress.ip_network(entry.strip(), strict=True)
    except ValueError:
        raise ValueError(GARBAGE) from None
    if network.prefixlen == 0:
        raise ValueError(EVERYTHING)
    if isinstance(network, ipaddress.IPv6Network) and network.subnet_of(_MAPPED):
        raise ValueError(MAPPED)
    return network


def proxy_network_problem(entry: str) -> str | None:
    """Why `entry` cannot be a trusted proxy (a fixed message), or None if it can."""
    try:
        parse_proxy_network(entry)
    except ValueError as error:
        return str(error)
    return None


def is_wide(network: IPNetwork) -> bool:
    """Wider than a /24 (IPv4) or a /64 (IPv6): more than one proxy is probably trusted."""
    if isinstance(network, ipaddress.IPv4Network):
        return network.prefixlen < NARROW_IPV4_PREFIX
    return network.prefixlen < NARROW_IPV6_PREFIX
