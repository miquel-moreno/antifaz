"""Who is asking and over what, for the panel's door (issue 53, ADR-0018): web/forwarded.py.

X-Forwarded-For and X-Forwarded-Proto count only from a trusted proxy; the client's address is
the first untrusted one walking X-Forwarded-For from the right; anything odd falls back to the
connection's own address, or to "not https".
"""

import ipaddress
from typing import Any

import pytest

from antifaz import trusted_networks
from antifaz.web.forwarded import (
    MAX_FORWARDED_BYTES,
    MAX_HOPS,
    client_ip,
    effective_scheme,
    is_local_host,
    parse_networks,
    peer_ip,
    request_host,
)

PROXY = "10.0.0.5"
TRUSTED = parse_networks(["10.0.0.0/24", "fd00::/64"])


def scope(
    peer: str | None = PROXY,
    headers: list[tuple[str, str]] | None = None,
    scheme: str = "http",
) -> dict[str, Any]:
    return {
        "type": "http",
        "scheme": scheme,
        "client": None if peer is None else (peer, 51234),
        "headers": [(k.lower().encode(), v.encode("latin-1")) for k, v in headers or []],
    }


def xff(*values: str, peer: str | None = PROXY) -> dict[str, Any]:
    return scope(peer, [("X-Forwarded-For", value) for value in values])


# --- parse_networks ----------------------------------------------------------------------------


def test_parse_networks_reads_ips_and_ranges() -> None:
    networks = parse_networks(["10.0.0.1", "172.16.0.0/12", "::1", " fd00::/8 "])

    assert networks == (
        ipaddress.ip_network("10.0.0.1/32"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("::1/128"),
        ipaddress.ip_network("fd00::/8"),
    )
    assert parse_networks([]) == ()


@pytest.mark.parametrize(
    "entry",
    [
        "172.16.5.4/12",
        "0.0.0.0/0",
        "::/0",
        "proxy",
        "",
        "1.2.3.4:80",
        "fe80::1%eth0",
        "::ffff:10.0.0.5",
        "::ffff:10.0.0.0/104",
    ],
)
def test_parse_networks_refuses_bad_entries_without_naming_them(entry: str) -> None:
    with pytest.raises(ValueError) as info:
        parse_networks([entry])

    message = str(info.value)
    assert message in (
        trusted_networks.GARBAGE,
        trusted_networks.EVERYTHING,
        trusted_networks.MAPPED,
    )
    if entry not in ("", "0.0.0.0/0", "::/0"):  # the /0 rule names those two itself
        assert entry not in message


def test_parse_networks_and_the_settings_check_share_one_rule() -> None:
    for entry in ["10.0.0.0/8", "10.0.0.1/8", "::ffff:10.0.0.5", "fd00::/64", "x", "::/0"]:
        problem = trusted_networks.proxy_network_problem(entry)
        try:
            parse_networks([entry])
        except ValueError as error:
            assert str(error) == problem
        else:
            assert problem is None


# --- peer_ip -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("peer", "expected"),
    [
        ("10.0.0.5", "10.0.0.5"),
        ("::1", "::1"),
        ("::ffff:10.0.0.5", "10.0.0.5"),
        ("testclient", None),
        (None, None),
        ("", None),
    ],
)
def test_peer_ip(peer: str | None, expected: str | None) -> None:
    address = peer_ip(scope(peer))

    assert (None if address is None else str(address)) == expected


def test_peer_ip_without_a_client_key() -> None:
    assert peer_ip({"type": "http", "headers": []}) is None


# --- client_ip -----------------------------------------------------------------------------------


def test_an_untrusted_peer_ignores_x_forwarded_for() -> None:
    assert client_ip(xff("198.51.100.7", peer="203.0.113.9"), TRUSTED) == "203.0.113.9"


def test_without_trusted_proxies_the_header_is_always_ignored() -> None:
    assert client_ip(xff("198.51.100.7"), ()) == PROXY


def test_a_trusted_peer_gives_the_client_it_saw() -> None:
    assert client_ip(xff("198.51.100.7"), TRUSTED) == "198.51.100.7"


def test_a_spoofed_left_entry_is_never_taken() -> None:
    # The client wrote "1.1.1.1"; the proxy appended the address it really saw.
    assert client_ip(xff("1.1.1.1, 198.51.100.7"), TRUSTED) == "198.51.100.7"


def test_three_chained_proxies_give_the_first_untrusted_from_the_right() -> None:
    header = "6.6.6.6, 198.51.100.7, 10.0.0.9, 10.0.0.8"
    assert client_ip(xff(header), TRUSTED) == "198.51.100.7"


def test_all_trusted_gives_the_peer() -> None:
    assert client_ip(xff("10.0.0.7, 10.0.0.8"), TRUSTED) == PROXY


@pytest.mark.parametrize(
    "header",
    [
        "",
        " ",
        ",",
        "198.51.100.7:0",
        "198.51.100.7:65536",
        "198.51.100.7:",
        "[2001:db8::1",
        "[2001:db8::1]x",
        "[198.51.100.7]",
        "2001:db8::1:443:x",
        "unknown",
        "_hidden",
        "fe80::1%eth0",
        "198.51.100.7 extra",
        "198.51.100.07",
        "not-an-ip",
    ],
)
def test_a_malformed_entry_where_proxies_write_gives_the_peer(header: str) -> None:
    assert client_ip(xff(header), TRUSTED) == PROXY


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("198.51.100.7:4711", "198.51.100.7"),
        ("[2001:db8::1]", "2001:db8::1"),
        ("[2001:db8::1]:443", "2001:db8::1"),
        ("[::ffff:198.51.100.7]:443", "198.51.100.7"),
        ("198.51.100.7,", "198.51.100.7"),
        ("198.51.100.7, 10.0.0.9,", "198.51.100.7"),
        ("198.51.100.7,, ,10.0.0.9", "198.51.100.7"),
        ("1.1.1.1, 198.51.100.7:1, 10.0.0.9:65535", "198.51.100.7"),
    ],
)
def test_ports_brackets_and_empty_elements_are_understood(header: str, expected: str) -> None:
    assert client_ip(xff(header), TRUSTED) == expected


def test_two_clients_behind_one_proxy_with_ports_are_different_buckets() -> None:
    first = client_ip(xff("198.51.100.7:50000"), TRUSTED)
    second = client_ip(xff("198.51.100.8:50000"), TRUSTED)
    assert first == "198.51.100.7" and second == "198.51.100.8"
    assert client_ip(xff("198.51.100.7:1"), TRUSTED) == client_ip(xff("198.51.100.7:2"), TRUSTED)


def test_garbage_left_of_the_client_is_never_parsed() -> None:
    # A client cannot push everyone onto the proxy's own address (and its login limit).
    assert client_ip(xff("garbage, unknown, 198.51.100.7"), TRUSTED) == "198.51.100.7"


def test_spaces_and_tabs_around_entries_are_allowed() -> None:
    assert client_ip(xff("1.1.1.1 ,\t198.51.100.7 \t"), TRUSTED) == "198.51.100.7"


def test_a_proxy_that_prepends_its_line_leaves_the_client_line_on_the_right() -> None:
    # Documented behaviour: lines are read in order (RFC 9110), so if a proxy PREPENDS its own
    # X-Forwarded-For line, the rightmost entry is the client's own line and is believed.
    # Only proxies that append (nginx, Caddy, Traefik, HAProxy do) may be trusted.
    prepended = xff("198.51.100.7", "6.6.6.6")
    assert client_ip(prepended, TRUSTED) == "6.6.6.6"


def test_repeated_headers_are_joined_in_order() -> None:
    joined = xff("1.1.1.1, 198.51.100.7", "10.0.0.9")
    assert client_ip(joined, TRUSTED) == "198.51.100.7"
    assert client_ip(xff("198.51.100.7", "203.0.113.4"), TRUSTED) == "203.0.113.4"


def test_ipv4_mapped_ipv6_is_read_as_ipv4() -> None:
    assert client_ip(xff("::ffff:198.51.100.7", peer="::ffff:10.0.0.5"), TRUSTED) == (
        "198.51.100.7"
    )
    # A mapped trusted proxy in the chain is still a trusted proxy.
    assert client_ip(xff("198.51.100.7, ::ffff:10.0.0.9"), TRUSTED) == "198.51.100.7"


def test_ipv6_proxies_and_clients() -> None:
    assert client_ip(xff("2001:db8::7, fd00::9", peer="fd00::1"), TRUSTED) == "2001:db8::7"


def test_the_test_client_and_a_missing_client() -> None:
    assert client_ip(xff("198.51.100.7", peer="testclient"), TRUSTED) == "testclient"
    assert client_ip(xff("198.51.100.7", peer=None), TRUSTED) == "unknown"


def test_other_headers_are_not_read() -> None:
    headers = [("X-Real-IP", "1.1.1.1"), ("Forwarded", "for=1.1.1.1")]
    assert client_ip(scope(PROXY, headers), TRUSTED) == PROXY


# --- effective_scheme --------------------------------------------------------------------------


def xfp(*values: str, peer: str = PROXY, scheme: str = "http") -> dict[str, Any]:
    return scope(peer, [("X-Forwarded-Proto", value) for value in values], scheme)


def test_an_untrusted_peer_ignores_x_forwarded_proto() -> None:
    assert effective_scheme(xfp("https", peer="203.0.113.9"), TRUSTED) == "http"
    assert effective_scheme(xfp("http", peer="203.0.113.9", scheme="https"), TRUSTED) == "https"
    assert effective_scheme(xfp("https", peer="testclient"), TRUSTED) == "http"


def test_a_trusted_proxy_sets_the_scheme() -> None:
    assert effective_scheme(xfp("https"), TRUSTED) == "https"
    assert effective_scheme(xfp("http", scheme="https"), TRUSTED) == "http"


def test_a_trusted_proxy_without_the_header_keeps_the_scheme() -> None:
    assert effective_scheme(scope(PROXY), TRUSTED) == "http"


@pytest.mark.parametrize(
    "values",
    [
        ("https", "https"),
        ("https", "http"),
        ("https, http",),
        ("https,https",),
        ("HTTPS",),
        (" https",),
        ("https ",),
        ("wss",),
        ("",),
    ],
)
def test_anything_but_one_exact_value_is_not_https(values: tuple[str, ...]) -> None:
    assert effective_scheme(xfp(*values), TRUSTED) == "http"
    assert effective_scheme(xfp(*values, scheme="https"), TRUSTED) == "http"


def test_a_scope_without_scheme_is_http() -> None:
    assert effective_scheme({"type": "http", "headers": []}, TRUSTED) == "http"


# --- request_host and is_local_host ------------------------------------------------------------


def test_request_host_is_the_single_host_header_lower_cased() -> None:
    assert request_host(scope(headers=[("Host", "LocalHost:8000")])) == "localhost:8000"
    assert request_host(scope(headers=[])) is None
    assert request_host(scope(headers=[("Host", "localhost"), ("Host", "evil.example")])) is None


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "LOCALHOST",
        "localhost:8000",
        "127.0.0.1",
        "127.0.0.1:8000",
        "[::1]",
        "[::1]:8000",
        "localhost:1",
        "localhost:65535",
        "[::1]:65535",
    ],
)
def test_local_hosts(host: str) -> None:
    assert is_local_host(host)


@pytest.mark.parametrize(
    "host",
    [
        None,
        "",
        "localhost.evil.com",
        "localhost.evil.com:8000",
        "evil.localhost",
        "127.0.0.2",
        "127.0.0.1.nip.io",
        "localhost.",
        "::1",
        "[::1",
        "[::1]x",
        "[::1]:",
        "[::2]",
        "localhost:",
        "localhost:80:80",
        "localhost:8o",
        "localhost:123456",
        "localhost:0",
        "localhost:65536",
        "localhost:99999",
        "[::1]:0",
        "localhost:\u0661\u0662",  # Arabic-Indic digits are digits, but not a port
        "0.0.0.0",  # noqa: S104 - a Host value to judge, not a bind address
        "antifaz",
        "localhost@evil.com",
    ],
)
def test_not_local_hosts(host: str | None) -> None:
    assert not is_local_host(host)


# --- Bounds: a huge or endlessly repeated header costs little (non-flaky: counts, not time) ----


class CountingHeaders(list[tuple[bytes, bytes]]):
    """A header list that counts how many items are read from it, from either end."""

    def __init__(self, items: list[tuple[bytes, bytes]]) -> None:
        super().__init__(items)
        self.read = 0

    def __reversed__(self):  # type: ignore[no-untyped-def]
        for item in super().__reversed__():
            self.read += 1
            yield item


def test_at_most_max_hops_entries_are_walked() -> None:
    trusted_chain = ", ".join(["10.0.0.9"] * (MAX_HOPS - 1))
    assert client_ip(xff(f"198.51.100.7, {trusted_chain}"), TRUSTED) == "198.51.100.7"
    too_long = ", ".join(["10.0.0.9"] * MAX_HOPS)
    # The client is past the walk: every entry looked at is trusted, so the peer is used.
    assert client_ip(xff(f"198.51.100.7, {too_long}"), TRUSTED) == PROXY


def test_a_2_mb_header_is_cut_to_its_rightmost_part_without_falling_back() -> None:
    huge = "x" * (2 * 1024 * 1024) + ", 198.51.100.7, 10.0.0.9"
    assert client_ip(xff(huge), TRUSTED) == "198.51.100.7"
    # Cut in the middle of an entry: the half entry is not read, the rest still counts.
    filler = ", ".join(["10.0.0.9"] * 3)
    cut = "1" * (MAX_FORWARDED_BYTES - len(filler) - 4) + "9.1.1.1, " + filler
    assert client_ip(xff(cut), TRUSTED) == PROXY  # all read entries trusted, half one dropped


def test_text_beyond_the_byte_budget_is_never_read() -> None:
    # Empty elements are skipped, so only the byte budget stops this walk before "6.6.6.6".
    padded = "6.6.6.6" + "," * MAX_FORWARDED_BYTES + "10.0.0.9"
    assert client_ip(xff(padded), TRUSTED) == PROXY
    near = "6.6.6.6" + "," * (MAX_FORWARDED_BYTES - 100) + "10.0.0.9"
    assert client_ip(xff(near), TRUSTED) == "6.6.6.6"


def test_100000_repeated_headers_are_read_from_the_right_and_stop_early() -> None:
    items = [(b"x-forwarded-for", b"6.6.6.6")] * 100_000 + [(b"x-forwarded-for", b"198.51.100.7")]
    headers = CountingHeaders(items)
    request = {"type": "http", "client": (PROXY, 1), "headers": headers}

    assert client_ip(request, TRUSTED) == "198.51.100.7"
    assert headers.read == 1

    trusted_lines = CountingHeaders([(b"x-forwarded-for", b"10.0.0.9")] * 100_000)
    request = {"type": "http", "client": (PROXY, 1), "headers": trusted_lines}
    assert client_ip(request, TRUSTED) == PROXY
    assert trusted_lines.read == MAX_HOPS


def test_headers_given_as_any_iterable_are_read_too() -> None:
    request = {
        "type": "http",
        "client": (PROXY, 1),
        "headers": iter([(b"x-forwarded-for", b"1.1.1.1, 198.51.100.7")]),
    }
    assert client_ip(request, TRUSTED) == "198.51.100.7"
