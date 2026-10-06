"""`antifaz doctor`: is the configuration right, is the gateway up, do the provider keys work?

Three checks, in order (issue 43, ADR-0017):

1. Configuration: the settings the gateway would read (the .env in --path, default the current
   folder, with ANTIFAZ_* environment variables on top, as pydantic-settings does) and
   `check_safe_to_start`. It names variables, never values, and says which providers have a
   key (names only) and whether the NER is on.
2. Gateway: `GET {url}/healthz` (public: no key is sent) with a Host the gateway accepts and
   never through a proxy (HTTP(S)_PROXY is ignored: the gateway is local), no redirects.
3. Only with --providers: `GET` the model list of each provider that has a key, straight to
   the provider (OpenAI `{base}/models` with Bearer; Anthropic `{base}/v1/models` with
   x-api-key and anthropic-version). Listing models is free. No redirects (a key must never
   follow one), a timeout, and the answer body is never read: only its status code. These
   requests DO honour HTTP(S)_PROXY, as the gateway's own client does, so a corporate proxy
   is checked on the same path the gateway uses.

Every message is fixed text: never a key, a body, an exception or a base URL (it could hold
a user:password). Exit codes: 0 every check passed, 1 something failed, 2 usage.

Forbidden: printing values, keys, bodies or exceptions. Sending the Antifaz key. Following
redirects. Calling a provider without --providers.
"""

import argparse
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TextIO
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from antifaz.config import Settings, UnsafeConfigError, _format_problem, check_safe_to_start
from antifaz.healthcheck import host_header

DEFAULT_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT = 5.0
MAX_TIMEOUT = 120.0
ANTHROPIC_VERSION = "2023-06-01"
DOCS_URL = "https://github.com/miquel-moreno/antifaz#quick-start"
MAX_HEALTH_BYTES = 64 * 1024
NER_STATES = frozenset({"ok", "starting", "circuit_open", "closed"})
_VERSION = re.compile(r"[0-9A-Za-z.+-]{1,40}")

Status = Literal["ok", "fail", "note"]
MARKS: dict[Status, str] = {"ok": "✓", "fail": "✗", "note": "-"}
ASCII_MARKS: dict[Status, str] = {"ok": "OK  ", "fail": "FAIL", "note": "--  "}


@dataclass
class Report:
    lines: list[tuple[Status, str]] = field(default_factory=list)

    def add(self, status: Status, text: str) -> None:
        self.lines.append((status, text))

    @property
    def failed(self) -> bool:
        return any(status == "fail" for status, _ in self.lines)


# --- Arguments --------------------------------------------------------------------------------


def _timeout(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("expects a number of seconds") from None
    if not 0 < seconds <= MAX_TIMEOUT:
        raise argparse.ArgumentTypeError(f"expects seconds between 0 and {MAX_TIMEOUT:g}")
    return seconds


def add_parser(commands: "argparse._SubParsersAction[Any]") -> None:
    command = commands.add_parser(
        "doctor",
        help="check the configuration, the running gateway and (with --providers) the "
        "provider keys; never prints a value or a key",
    )
    command.add_argument(
        "--path", default=".", help="folder with the .env to check (default: current folder)"
    )
    command.add_argument(
        "--url", default=DEFAULT_URL, help=f"where the gateway listens (default: {DEFAULT_URL})"
    )
    command.add_argument(
        "--providers",
        action="store_true",
        help="also list the models of each provider with a key (free; checks the key works)",
    )
    command.add_argument(
        "--timeout",
        type=_timeout,
        default=DEFAULT_TIMEOUT,
        metavar="S",
        help=f"seconds to wait for each answer (default: {DEFAULT_TIMEOUT:g})",
    )


def _gateway_url(value: str) -> str | None:
    """`value` without a trailing "/", or None if it is not a plain http(s)://host[:port][/path]."""
    try:
        parts = urlsplit(value)
        parts.port  # noqa: B018 - raises ValueError on a bad port
    except ValueError:
        return None
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or "@" in parts.netloc
        or parts.query
        or parts.fragment
    ):
        return None
    return value.rstrip("/")


# --- 1. Configuration -------------------------------------------------------------------------


def _variable(field_name: str) -> str:
    name = field_name.upper()
    return name if name.startswith("ANTIFAZ_") else f"ANTIFAZ_{name}"


def load_settings(folder: Path) -> tuple[Settings | None, list[str]]:
    """The settings from folder/.env and the environment, or the variables that are wrong."""
    try:
        return Settings(_env_file=folder / ".env"), []
    except ValidationError as error:
        # Only the field names: the error itself repeats the values it got.
        fields = sorted({str(detail["loc"][0]) for detail in error.errors() if detail["loc"]})
        return None, [_variable(name) for name in fields]


def configured_providers(settings: Settings) -> list[tuple[str, str]]:
    """(name, key variable) of each provider with a key."""
    providers = []
    if settings.openai_api_key is not None:
        providers.append(("OpenAI", "ANTIFAZ_OPENAI_API_KEY"))
    if settings.anthropic_api_key is not None:
        providers.append(("Anthropic", "ANTIFAZ_ANTHROPIC_API_KEY"))
    return providers


def check_config(folder: Path, report: Report) -> Settings | None:
    found = (folder / ".env").is_file()
    settings, wrong = load_settings(folder)
    if settings is None:
        report.add("fail", "configuration: cannot read; check " + ", ".join(wrong))
        return None
    try:
        check_safe_to_start(settings)
    except UnsafeConfigError as error:  # its message names the variable, never the value
        report.add("fail", f"configuration: the gateway would not start: {error}")
    else:
        report.add("ok", "configuration: the gateway would start with it")
    if not found:
        report.add("note", "no .env in that folder: only environment variables were read")
    names = [name for name, _ in configured_providers(settings)]
    report.add(
        "note",
        "providers with a key: "
        + (", ".join(names) if names else "none (every proxy route answers 503)"),
    )
    report.add("note", f"NER (names and addresses): {'on' if settings.ner_enabled else 'off'}")
    return settings


# --- 2. Gateway -------------------------------------------------------------------------------


def _gateway_transport() -> httpx.BaseTransport | None:
    """None: the real network. Tests replace it with an httpx.MockTransport."""
    return None


def gateway_client(timeout: float) -> httpx.Client:
    # trust_env=False: HTTP(S)_PROXY and NO_PROXY are ignored, the gateway is reached directly.
    return httpx.Client(
        timeout=timeout, follow_redirects=False, trust_env=False, transport=_gateway_transport()
    )


def _matches(host: str, allowed: str) -> bool:
    if allowed.startswith("*."):
        return host.endswith(allowed[1:])
    return host == allowed


def choose_host(url: str, allowed_hosts: Sequence[str] | None) -> str:
    """The URL's own host if the gateway accepts it; else the first allowed host that is not a
    wildcard (or the first wildcard, as the container healthcheck does)."""
    hostname = urlsplit(url).hostname or ""
    host = f"[{hostname}]" if ":" in hostname else hostname
    if allowed_hosts is None or any(_matches(host, entry) for entry in allowed_hosts):
        return host
    exact = [entry for entry in allowed_hosts if "*" not in entry]
    return exact[0] if exact else host_header(allowed_hosts)


def _health_body(response: httpx.Response) -> dict[str, Any] | None:
    data = b""
    for chunk in response.iter_bytes():
        data += chunk
        if len(data) > MAX_HEALTH_BYTES:
            return None
    try:
        body = json.loads(data)
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def check_gateway(
    url: str, host: str, timeout: float, settings: Settings | None, report: Report
) -> bool:
    """Report the gateway's state. True if it answered at all."""
    try:
        with (
            gateway_client(timeout) as client,
            client.stream("GET", f"{url}/healthz", headers={"Host": host}) as response,
        ):
            status = response.status_code
            body = _health_body(response) if status == 200 else None
    except httpx.TimeoutException:
        report.add("fail", f"gateway: no answer within {timeout:g} s")
        return False
    except Exception:  # never shown: it could repeat the URL
        report.add("fail", "gateway: cannot connect")
        return False
    if status == 400:
        report.add(
            "fail",
            "gateway: refused the Host header (HTTP 400): its ANTIFAZ_ALLOWED_HOSTS does not "
            "match this configuration (restart it after changing .env)",
        )
        return True
    if status != 200 or body is None or body.get("status") != "ok":
        report.add("fail", f"gateway: /healthz did not answer ok (HTTP {status})")
        return True
    version = body.get("version")
    shown = version if isinstance(version, str) and _VERSION.fullmatch(version) else "unknown"
    report.add("ok", f"gateway: up, version {shown}")
    ner = body.get("ner")
    if ner is None:
        report.add("note", "gateway NER: off")
        if settings is not None and settings.ner_enabled:
            report.add(
                "note",
                "the configuration has the NER on but the gateway runs without it: restart it "
                "after changing .env (docker compose up -d)",
            )
    elif ner == "ok":
        report.add("ok", "gateway NER: ok")
    elif ner == "starting":
        report.add("note", "gateway NER: starting (the model may still be loading)")
    elif ner in NER_STATES:
        report.add("fail", f"gateway NER: {ner} (requests are blocked; see docker compose logs)")
    else:
        report.add("fail", "gateway NER: unknown state")
    return True


# --- 3. Providers -----------------------------------------------------------------------------


def _provider_transport() -> httpx.BaseTransport | None:
    """None: the real network. Tests replace it with an httpx.MockTransport."""
    return None


def provider_client(timeout: float) -> httpx.Client:
    # Like the gateway's own client (providers/http.py): no redirects, proxy settings honoured.
    return httpx.Client(timeout=timeout, follow_redirects=False, transport=_provider_transport())


def _provider_request(name: str, key: str, settings: Settings) -> tuple[str, dict[str, str], str]:
    """(url, headers, base URL variable) for the model list of `name`."""
    if name == "OpenAI":
        url = settings.openai_base_url.rstrip("/") + "/models"
        return url, {"Authorization": f"Bearer {key}"}, "ANTIFAZ_OPENAI_BASE_URL"
    url = settings.anthropic_base_url.rstrip("/") + "/v1/models"
    headers = {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
    return url, headers, "ANTIFAZ_ANTHROPIC_BASE_URL"


def _provider_status(
    name: str, key_variable: str, settings: Settings, timeout: float
) -> tuple[Status, str]:
    secret = settings.openai_api_key if name == "OpenAI" else settings.anthropic_api_key
    if secret is None or _format_problem(secret.get_secret_value()) is not None:
        return "fail", f"{name}: not checked: fix {key_variable} first"
    url, headers, base_variable = _provider_request(name, secret.get_secret_value(), settings)
    try:
        with (
            provider_client(timeout) as client,
            client.stream("GET", url, headers=headers) as response,
        ):
            status = response.status_code  # the body is never read: it could echo the key
    except httpx.TimeoutException:
        return "fail", f"{name}: no answer within {timeout:g} s (check {base_variable})"
    except (httpx.InvalidURL, httpx.UnsupportedProtocol):
        return "fail", f"{name}: {base_variable} is not a valid http(s) URL"
    except Exception:  # never shown: it could repeat the URL or a header
        return "fail", f"{name}: cannot reach the provider (check {base_variable} and the network)"
    if status == 200:
        return "ok", f"{name}: the key works (model list answered)"
    if status in (401, 403):
        return "fail", f"{name}: the provider refused the key (HTTP {status}): check {key_variable}"
    if 300 <= status < 400:
        return "fail", (
            f"{name}: the provider answered with a redirect (HTTP {status}), not followed: "
            f"check {base_variable}"
        )
    return "fail", f"{name}: unexpected answer (HTTP {status})"


def check_providers(settings: Settings | None, timeout: float, report: Report) -> None:
    if settings is None:
        report.add("fail", "providers: not checked: the configuration cannot be read")
        return
    providers = configured_providers(settings)
    if not providers:
        report.add("note", "providers: none has a key, nothing to check")
        return
    report.add("note", "providers: listing models (free, nothing is generated)")
    for name, key_variable in providers:
        report.add(*_provider_status(name, key_variable, settings, timeout))


# --- Output -----------------------------------------------------------------------------------


def marks_for(stream: TextIO) -> dict[Status, str]:
    """Check marks if the console can print them (cp1252 cannot), else OK / FAIL."""
    encoding = getattr(stream, "encoding", None) or "ascii"
    try:
        "".join(MARKS.values()).encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return ASCII_MARKS
    return MARKS


def _print(report: Report, gateway_up: bool, out: TextIO) -> None:
    marks = marks_for(out)
    lines = ["antifaz doctor"]
    lines += [f"  {marks[status]} {text}" for status, text in report.lines]
    lines.append("")
    lines.append("antifaz doctor: " + ("FAIL" if report.failed else "all checks passed"))
    lines.append("Next steps:")
    if not gateway_up:
        lines.append("  docker compose up -d            start the gateway (is it running?)")
    lines += [
        "  antifaz verify                  plant fake data and check none reaches a provider",
        "  antifaz setup claude-code       point Claude Code at Antifaz (coming soon)",
        f"  docs: {DOCS_URL}",
    ]
    print("\n".join(lines), file=out)


def run(args: argparse.Namespace) -> int:
    folder = Path(args.path)
    url = _gateway_url(args.url)
    if not folder.is_dir():
        print("antifaz doctor: the folder given with --path does not exist", file=sys.stderr)
        return 2
    if url is None:
        print(
            "antifaz doctor: --url expects http(s)://host[:port], without user, query or fragment",
            file=sys.stderr,
        )
        return 2
    report = Report()
    settings = check_config(folder, report)
    host = choose_host(url, settings.allowed_hosts if settings is not None else None)
    gateway_up = check_gateway(url, host, args.timeout, settings, report)
    if args.providers:
        check_providers(settings, args.timeout, report)
    _print(report, gateway_up, sys.stdout)
    return 1 if report.failed else 0


__all__ = ["add_parser", "choose_host", "marks_for", "run"]
