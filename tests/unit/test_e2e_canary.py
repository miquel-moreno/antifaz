"""How the e2e canary judges what the REACH_OUT probe saw (tests/e2e/egress.py).

Every destination must come back in a result from an allow-list; anything else fails, so an
unknown error, a timeout or a missing line can never pass by accident. A literal IP never
involves DNS: `dns-failed` there means the probe did not even try (the IPv6 brackets were not
stripped), so it fails.
"""

import json

import pytest

from tests.e2e.egress import canary_problems, is_literal, parse_reach, split_target

NAMES = ["api.openai.com:443", "api.anthropic.com:443"]
LITERALS = ["1.1.1.1:443", "8.8.8.8:443", "[2606:4700:4700::1111]:443", "172.17.0.1:3128"]


def _line(target: str, result: str, errno: str | None = None) -> str:
    return json.dumps({"target": target, "result": result, "errno": errno})


def _found(**overrides: tuple[str, str | None]) -> dict[str, tuple[str, str | None]]:
    found: dict[str, tuple[str, str | None]] = {t: ("dns-failed", None) for t in NAMES}
    found |= {t: ("unreachable", "ENETUNREACH") for t in LITERALS}
    found["[2606:4700:4700::1111]:443"] = ("no-address", "EADDRNOTAVAIL")
    found |= overrides
    return found


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("api.openai.com:443", ("api.openai.com", 443)),
        ("1.1.1.1:443", ("1.1.1.1", 443)),
        ("[2606:4700:4700::1111]:443", ("2606:4700:4700::1111", 443)),
    ],
)
def test_split_target_strips_the_ipv6_brackets(target: str, expected: tuple[str, int]) -> None:
    assert split_target(target) == expected


def test_literal_ips_are_told_apart_from_names() -> None:
    assert [is_literal(t) for t in NAMES] == [False, False]
    assert all(is_literal(t) for t in LITERALS)


def test_parse_reach_reads_one_json_line_per_target() -> None:
    stdout = "\n".join([_line("1.1.1.1:443", "unreachable", "ENETUNREACH"), ""])
    assert parse_reach(stdout) == {"1.1.1.1:443": ("unreachable", "ENETUNREACH")}


def test_unreachable_everywhere_passes() -> None:
    assert canary_problems(_found(), NAMES + LITERALS) == []


def test_a_name_may_also_fail_to_connect() -> None:
    found = _found(**{"api.openai.com:443": ("unreachable", "ENETUNREACH")})
    assert canary_problems(found, NAMES + LITERALS) == []


@pytest.mark.parametrize(
    ("target", "seen"),
    [
        ("api.openai.com:443", ("connected", None)),
        ("1.1.1.1:443", ("connected", None)),
        ("1.1.1.1:443", ("dns-failed", None)),  # a literal never needs DNS: the probe did not try
        ("[2606:4700:4700::1111]:443", ("dns-failed", None)),
        ("8.8.8.8:443", ("timeout", None)),  # a SYN left the container: a route exists
        ("8.8.8.8:443", ("refused", "ECONNREFUSED")),  # something answered
        ("172.17.0.1:3128", ("refused", "ECONNREFUSED")),  # the host answered
        ("api.anthropic.com:443", ("error", "EPERM")),  # unknown: fail closed
        ("api.anthropic.com:443", ("timeout", None)),
        ("1.1.1.1:443", ("whatever", None)),
    ],
)
def test_anything_outside_the_allow_list_fails(target: str, seen: tuple[str, str | None]) -> None:
    problems = canary_problems(_found(**{target: seen}), NAMES + LITERALS)
    assert len(problems) == 1
    assert target in problems[0] and seen[0] in problems[0]


def test_a_destination_the_probe_did_not_report_fails() -> None:
    found = _found()
    del found["8.8.8.8:443"]
    problems = canary_problems(found, NAMES + LITERALS)
    assert problems == ["8.8.8.8:443: no result from the probe"]


def test_no_targets_is_a_problem() -> None:
    assert canary_problems({}, []) != []
