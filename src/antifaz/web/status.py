"""What the panel's «Estado» view may say about the gateway (issue 53, PR 11c-3b, ADR-0018).

A pure piece (standard library only): the gateway builds a StatusFacts once, in api/app.py
`_panel()`, from its settings, and hands it to the panel inside PanelState. The settings never
reach antifaz.web. StatusFacts holds only booleans, counts, enums, the version, the checked
host of a custom provider address and a callable that reads the NER state: never a key, a token,
a base URL or any other free text (tests/unit/test_web_status.py).

- `provider_address(url, default)`: "default" if the URL is the shipped one (a trailing "/"
  aside); else only the host name and port of a custom URL (lowercased, IDNA in ASCII, an IPv4
  or a bracketed IPv6) that passes HOST; else "custom". Never the user part, path, query or
  fragment. `has_credentials` is True when the URL has a user part (the view warns about it).
- `ner_state(raw)`: None (no NER) is "off"; the NER pool's four states pass; anything else is
  "unknown", never the raw value.
- `overall(ner, providers)`: "danger" if the NER is closed; "warn" if it is starting, paused
  after failures (circuit_open) or unknown, if no provider has a key or if a provider URL has a
  user part; else "ok".
- `snapshot(facts, scheme)`: the JSON of GET /panel/api/status (its closed schema is
  web/status_response.py). The NER state is read at that moment; the uptime comes from the
  injected clock.

Forbidden: putting a base URL, a key or any text from the settings in StatusFacts; showing the
raw NER state; reading the settings here.
"""

import ipaddress
import re
import time
import typing
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlsplit

NerState = Literal["off", "ok", "starting", "circuit_open", "closed", "unknown"]
ProviderId = Literal["openai", "anthropic"]
Overall = Literal["ok", "warn", "danger"]

NER_STATES: frozenset[str] = frozenset(typing.get_args(NerState))
# The NER pool's states (detect/ner/pool.py status()), each to itself.
_POOL_STATES: dict[str, NerState] = {
    "ok": "ok",
    "starting": "starting",
    "circuit_open": "circuit_open",
    "closed": "closed",
}
SCHEMA_VERSION = 1
MAX_URL_CHARACTERS = 2048
MAX_NAME_CHARACTERS = 253

# One pattern for Python (fullmatch) and for the JSON schema (pydantic's regex, anchored there):
# ASCII classes only, no look-around.
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_PORT = (
    r"(?::(?:6553[0-5]|655[0-2][0-9]|65[0-4][0-9]{2}|6[0-4][0-9]{3}|[1-5][0-9]{4}"
    r"|[1-9][0-9]{0,3}))?"
)
HOST_PATTERN = rf"(?:{_LABEL}(?:\.{_LABEL})*|\[[0-9a-f:.]{{2,45}}\]){_PORT}"
HOST = re.compile(HOST_PATTERN)
VERSION_PATTERN = r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}(?:[-+.]?[0-9A-Za-z][0-9A-Za-z.+-]{0,31})?"
_AUTHORITY_END = re.compile(r"[/?#\\]")


def _has_user_part(url: str) -> bool:
    """Whether the authority (after "//", up to the first / ? # or backslash) has an "@"."""
    rest = url.split("//", 1)[1] if "//" in url else url
    return "@" in _AUTHORITY_END.split(rest, maxsplit=1)[0]


def _host(url: str) -> str | None:
    """The checked host[:port] of an http(s) URL, or None."""
    odd = any(char.isspace() or not char.isprintable() for char in url)
    if len(url) > MAX_URL_CHARACTERS or odd or "%" in url or "\\" in url:
        return None
    try:
        parts = urlsplit(url)
        name = parts.hostname
        port = parts.port
        if parts.scheme not in ("http", "https") or not name:
            return None
        if ":" in name:
            host = f"[{ipaddress.IPv6Address(name).compressed}]"
        elif name.replace(".", "").isdigit():
            host = str(ipaddress.IPv4Address(name))
        else:
            host = name.encode("idna").decode("ascii").lower()
            if len(host) > MAX_NAME_CHARACTERS:
                return None
    except ValueError:  # a bad port, IPv6, IPv4 or IDNA label (UnicodeError is a ValueError)
        return None
    address = host if port is None else f"{host}:{port}"
    return address if HOST.fullmatch(address) else None


def provider_address(url: str, default: str) -> tuple[str, bool]:
    """(address, has_credentials): "default", the checked host[:port], or "custom"."""
    if url.rstrip("/") == default.rstrip("/"):
        return "default", False
    return _host(url) or "custom", _has_user_part(url)


def ner_state(raw: object) -> NerState:
    """The NER state for the panel: "off" for None, the pool's states, else "unknown"."""
    if raw is None:
        return "off"
    return _POOL_STATES.get(raw, "unknown") if isinstance(raw, str) else "unknown"


def _checked_address(address: str) -> bool:
    return address in ("default", "custom") or HOST.fullmatch(address) is not None


@dataclass(frozen=True)
class ProviderFacts:
    id: ProviderId
    has_key: bool
    address: str  # "default", "custom" or a host that passes HOST: checked here
    has_credentials: bool

    def __post_init__(self) -> None:
        if not _checked_address(self.address):
            raise ValueError("a provider address must be default, custom or a checked host")


def overall(ner: NerState, providers: tuple[ProviderFacts, ...]) -> Overall:
    if ner == "closed":
        return "danger"
    if ner in ("starting", "circuit_open", "unknown"):
        return "warn"
    if not any(p.has_key for p in providers) or any(p.has_credentials for p in providers):
        return "warn"
    return "ok"


@dataclass(frozen=True)
class StatusFacts:
    """The gateway's status without a secret: built once at startup, read per request."""

    version: str
    providers: tuple[ProviderFacts, ...]
    allowed_hosts: int
    allowed_origins: int
    trusted_proxies: int
    ner_state: Callable[[], object] = field(repr=False)  # None without the NER, else its state
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)
    started: float = field(default=-1.0)

    def __post_init__(self) -> None:
        if min(self.allowed_hosts, self.allowed_origins, self.trusted_proxies) < 0:
            raise ValueError("a count cannot be negative")
        if self.started < 0:
            object.__setattr__(self, "started", self.clock())

    def uptime_seconds(self) -> int:
        """Whole seconds since the facts were built (never negative)."""
        return max(0, int(self.clock() - self.started))


def snapshot(facts: StatusFacts, scheme: str) -> dict[str, Any]:
    """The JSON of GET /panel/api/status for a request over `scheme` (the door's decision)."""
    ner = ner_state(facts.ner_state())
    return {
        "schema": SCHEMA_VERSION,
        "version": facts.version,
        "uptime_seconds": facts.uptime_seconds(),
        "panel_scheme": "https" if scheme == "https" else "http_localhost",
        "detectors": {"validators": "on", "patterns": "on", "ner": ner},
        "providers": [
            {
                "id": provider.id,
                "has_key": provider.has_key,
                "address": provider.address,
                "has_credentials": provider.has_credentials,
            }
            for provider in facts.providers
        ],
        "limits": {
            "allowed_hosts": facts.allowed_hosts,
            "allowed_origins": facts.allowed_origins,
            "trusted_proxies": facts.trusted_proxies,
        },
        "overall": overall(ner, facts.providers),
    }
