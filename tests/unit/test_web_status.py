"""The facts behind the panel's «Estado» view (issue 53, PR 11c-3b, ADR-0018).

- `provider_address`: what the panel may say about a provider's base URL. "default" if it is the
  shipped one; else only the host name (and port) of a custom URL, lowercased, IDNA in ASCII and
  checked by a strict pattern, or "custom". Never the user part, path, query or fragment;
  `has_credentials` says the URL carries a user part (the view shows a warning).
- `ner_state`: "off" without the NER, its four states, and "unknown" for anything else.
- `overall`: the truth table of the gateway card.
- `StatusFacts`: booleans, counts, enums, the version, a checked host and a callable for the NER
  state; never a SecretStr or a free text. The uptime clock is injected.
- `StatusResponse`: the closed JSON schema (extra fields and unknown enums are refused).
"""

import dataclasses
import re
import typing
from collections.abc import Callable

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import SecretStr, ValidationError

from antifaz import __version__
from antifaz.web.status import (
    HOST,
    NER_OFF,
    NER_STATES,
    ProviderFacts,
    StatusFacts,
    ner_state,
    overall,
    provider_address,
    snapshot,
)
from antifaz.web.status_response import StatusResponse

OPENAI = "https://api.openai.com/v1"
ANTHROPIC = "https://api.anthropic.com"


@pytest.mark.parametrize(
    ("url", "default", "expected"),
    [
        (OPENAI, OPENAI, ("default", False)),
        (OPENAI + "/", OPENAI, ("default", False)),
        (ANTHROPIC, ANTHROPIC, ("default", False)),
        ("https://proxy.example/v1", OPENAI, ("proxy.example", False)),
        ("https://Proxy.EXAMPLE:8443/v1", OPENAI, ("proxy.example:8443", False)),
        ("http://10.0.0.7:8080", OPENAI, ("10.0.0.7:8080", False)),
        ("http://192.168.1.20", ANTHROPIC, ("192.168.1.20", False)),
        ("http://[::1]:11434/v1", OPENAI, ("[::1]:11434", False)),
        ("http://[2001:DB8::0:1]/v1", OPENAI, ("[2001:db8::1]", False)),
        ("https://api.openai.com/v2", OPENAI, ("api.openai.com", False)),
        # A user part: only the bare host, and the warning.
        ("https://user:CanaryPass@proxy.example/v1", OPENAI, ("proxy.example", True)),
        ("https://user@proxy.example:9000", OPENAI, ("proxy.example:9000", True)),
        ("https://u:p@[::1]:1/", OPENAI, ("[::1]:1", True)),
        # Path, query and fragment are dropped.
        (
            "https://proxy.example/v1/CanaryPath?key=CanaryQuery#CanaryFragment",
            OPENAI,
            ("proxy.example", False),
        ),
        ("https://proxy.example?key=x@y", OPENAI, ("proxy.example", False)),
        ("https://proxy.example/a@b", OPENAI, ("proxy.example", False)),
        # IDN in ASCII (punycode).
        ("https://bücher.example/v1", OPENAI, ("xn--bcher-kva.example", False)),
        # Anything odd: "custom", never the raw text.
        ("not a url", OPENAI, ("custom", False)),
        ("", OPENAI, ("custom", False)),
        ("ftp://proxy.example", OPENAI, ("custom", False)),
        ("https://", OPENAI, ("custom", False)),
        ("https://proxy.example:0/", OPENAI, ("custom", False)),
        ("https://proxy.example:99999/", OPENAI, ("custom", False)),
        ("https://proxy.example:abc/", OPENAI, ("custom", False)),
        ("https://prox%79.example/", OPENAI, ("custom", False)),
        ("https://proxy .example/", OPENAI, ("custom", False)),
        ("https://proxy.example\t/", OPENAI, ("custom", False)),
        ("https://proxy.\x00example/", OPENAI, ("custom", False)),
        ("https://proxy.example\u2028/", OPENAI, ("custom", False)),
        ("https://pro_xy.example/", OPENAI, ("custom", False)),
        ("https://-proxy.example/", OPENAI, ("custom", False)),
        ("https://proxy..example/", OPENAI, ("custom", False)),
        ("https://999.1.1.1/", OPENAI, ("custom", False)),
        ("https://[::1%25eth0]/", OPENAI, ("custom", False)),
        ("https://[nope]/", OPENAI, ("custom", False)),
        ("https://" + "a" * 64 + ".example/", OPENAI, ("custom", False)),
        ("https://" + ".".join(["abcdefghij"] * 26) + "/", OPENAI, ("custom", False)),
        ("https://proxy.example/" + "x" * 5000, OPENAI, ("custom", False)),
        ("https://user:pa ss@proxy.example/", OPENAI, ("custom", True)),
    ],
)
def test_provider_address(url: str, default: str, expected: tuple[str, bool]) -> None:
    assert provider_address(url, default) == expected


@given(st.text(max_size=300), st.sampled_from([OPENAI, ANTHROPIC]))
def test_provider_address_is_always_an_enum_or_a_strict_host(url: str, default: str) -> None:
    address, _ = provider_address(url, default)

    assert address in ("default", "custom") or HOST.fullmatch(address)
    assert not set(address) & set("@/?#%\\ ")


@given(
    st.from_regex(r"[a-z]{1,10}(\.[a-z]{1,10}){0,3}", fullmatch=True),
    st.text(alphabet="abcXYZ019-._~:", min_size=1, max_size=20),
    st.text(alphabet="abcXYZ019-._~/=&", max_size=30),
)
def test_a_user_part_path_or_query_never_shows(host: str, secret: str, tail: str) -> None:
    url = f"https://u{secret}@{host}/{tail}?{tail}#{tail}"
    address, credentials = provider_address(url, OPENAI)

    assert credentials
    assert address in (host, "custom")


@pytest.mark.parametrize(
    ("raw", "state"),
    [
        (NER_OFF, "off"),
        (None, "unknown"),
        ("ok", "ok"),
        ("starting", "starting"),
        ("circuit_open", "circuit_open"),
        ("closed", "closed"),
        ("off", "unknown"),
        ("OK", "unknown"),
        ("<script>", "unknown"),
        (3, "unknown"),
    ],
)
def test_ner_state(raw: object, state: str) -> None:
    assert ner_state(raw) == state
    assert ner_state(raw) in NER_STATES


def _provider(
    has_key: bool = True, credentials: bool = False, id_: str = "openai"
) -> ProviderFacts:
    return ProviderFacts(id_, has_key, "default", credentials)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("ner", "providers", "expected"),
    [
        ("off", [_provider()], "ok"),
        ("ok", [_provider(), _provider(False, id_="anthropic")], "ok"),
        ("starting", [_provider()], "warn"),
        ("circuit_open", [_provider()], "warn"),
        ("unknown", [_provider()], "warn"),
        ("closed", [_provider()], "danger"),
        ("off", [_provider(False), _provider(False, id_="anthropic")], "warn"),
        ("off", [], "warn"),
        ("ok", [_provider(credentials=True)], "warn"),
        ("closed", [_provider(credentials=True)], "danger"),
        ("closed", [_provider(False)], "danger"),
    ],
)
def test_overall(ner: str, providers: list[ProviderFacts], expected: str) -> None:
    assert overall(ner, tuple(providers)) == expected  # type: ignore[arg-type]


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _facts(
    clock: Callable[[], float], ner: Callable[[], object] = lambda: NER_OFF, **values: object
) -> StatusFacts:
    fields: dict[str, object] = {
        "version": __version__,
        "providers": (
            ProviderFacts("openai", True, "default", False),
            ProviderFacts("anthropic", False, "proxy.example:8443", True),
        ),
        "allowed_hosts": 3,
        "allowed_origins": 0,
        "trusted_proxies": 1,
        "ner_state": ner,
        "clock": clock,
        **values,
    }
    return StatusFacts(**fields)  # type: ignore[arg-type]


def test_the_uptime_comes_from_the_injected_clock() -> None:
    clock = _Clock()
    facts = _facts(clock)

    assert facts.uptime_seconds() == 0
    clock.now += 59.9
    assert facts.uptime_seconds() == 59
    clock.now += 3 * 86400
    assert facts.uptime_seconds() == 3 * 86400 + 59
    clock.now -= 10**6  # a clock that went back never gives a negative uptime
    assert facts.uptime_seconds() == 0


def test_the_snapshot_has_exactly_the_schema_and_reads_the_ner_each_time() -> None:
    states = iter(["starting", "ok"])
    facts = _facts(_Clock(), ner=lambda: next(states))

    first = snapshot(facts, "https")
    second = snapshot(facts, "http")

    assert first == {
        "schema": 1,
        "version": __version__,
        "uptime_seconds": 0,
        "panel_scheme": "https",
        "detectors": {"validators": "on", "patterns": "on", "ner": "starting"},
        "providers": [
            {"id": "openai", "has_key": True, "address": "default", "has_credentials": False},
            {
                "id": "anthropic",
                "has_key": False,
                "address": "proxy.example:8443",
                "has_credentials": True,
            },
        ],
        "limits": {"allowed_hosts": 3, "allowed_origins": 0, "trusted_proxies": 1},
        "overall": "warn",
    }
    assert second["detectors"]["ner"] == "ok"  # type: ignore[index]
    assert second["panel_scheme"] == "http_localhost"
    StatusResponse.model_validate(first)
    StatusResponse.model_validate(second)


@pytest.mark.parametrize(
    "address", ["", "Proxy.example", "a@b", "x/y", "proxy.example?x", "custom!"]
)
def test_provider_facts_refuse_an_address_that_is_not_checked(address: str) -> None:
    with pytest.raises(ValueError, match="address"):
        ProviderFacts("openai", True, address, False)


def test_status_facts_refuse_negative_counts() -> None:
    with pytest.raises(ValueError, match="count"):
        _facts(_Clock(), allowed_hosts=-1)


ALLOWED_TEXT_FIELDS = {("StatusFacts", "version"), ("ProviderFacts", "address")}


@pytest.mark.parametrize("cls", [StatusFacts, ProviderFacts])
def test_the_facts_hold_no_secret_and_no_free_text(cls: type) -> None:
    hints = typing.get_type_hints(cls)
    for field in dataclasses.fields(cls):
        hint = hints[field.name]
        assert SecretStr not in (hint, *typing.get_args(hint)), field.name
        if hint is str:
            assert (cls.__name__, field.name) in ALLOWED_TEXT_FIELDS, field.name
        elif typing.get_origin(hint) is typing.Literal:
            assert all(isinstance(value, str) for value in typing.get_args(hint))
        else:
            allowed = (bool, int, float, tuple, Callable)
            origin = typing.get_origin(hint) or hint
            assert origin in allowed or getattr(origin, "__name__", "") == "Callable", field.name


# --- the closed schema ----------------------------------------------------------------------


def _valid() -> dict[str, typing.Any]:
    return snapshot(_facts(_Clock(), ner=lambda: "ok"), "https")


def test_the_schema_accepts_a_snapshot() -> None:
    assert StatusResponse.model_validate(_valid()).overall == "warn"


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(extra=1),
        lambda d: d["detectors"].update(extra=1),
        lambda d: d["providers"][0].update(key="x"),
        lambda d: d["limits"].update(extra=1),
        lambda d: d.update(schema=2),
        lambda d: d.update(overall="fine"),
        lambda d: d.update(panel_scheme="http"),
        lambda d: d["detectors"].update(ner="sleeping"),
        lambda d: d["detectors"].update(validators="off"),
        lambda d: d["providers"][0].update(id="ollama"),
        lambda d: d["providers"][0].update(address="https://proxy.example/v1"),
        lambda d: d["providers"][0].update(address="u:p@proxy.example"),
        lambda d: d["providers"][0].update(address="proxy.example\n"),
        lambda d: d["providers"][0].update(has_key="yes"),
        lambda d: d.update(uptime_seconds=-1),
        lambda d: d.update(uptime_seconds=1.5),
        lambda d: d.update(uptime_seconds="1"),
        lambda d: d.update(version="0.1.1 <b>"),
        lambda d: d["limits"].update(allowed_hosts=True),
        lambda d: d.pop("overall"),
    ],
)
def test_the_schema_refuses_anything_else(change: Callable[[dict[str, typing.Any]], None]) -> None:
    data = _valid()
    change(data)

    with pytest.raises(ValidationError):
        StatusResponse.model_validate(data)


def test_the_version_pattern_takes_the_real_version() -> None:
    assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+.*", __version__)
    StatusResponse.model_validate({**_valid(), "version": "1.0.0rc1"})
