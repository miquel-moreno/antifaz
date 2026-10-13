"""`antifaz verify`: plant synthetic personal data and prove none of it reaches the provider.

It uses your configuration (ANTIFAZ_* variables and .env) and NEVER calls a provider: the
gateway runs in this process (httpx.ASGITransport) and its upstream is a fake
(httpx.MockTransport) that records every byte it receives and answers with the placeholders
it saw. For the run, the provider URLs and keys and the panel token are replaced by fake ones
(so a missing or real provider key makes no difference) and one extra Host name is allowed.

A battery of synthetic values (DNI, NIE, IBAN, email, phone, card) goes into every place a
client can put text, through both endpoints, with and without streaming. It checks that:

1. no planted value (in any spacing or case) and not the Antifaz key reach the fake provider;
2. no configured key comes back in any answer (one probe per endpoint makes the fake
   provider echo the headers it got, like a careless provider);
3. the answer brings every value back (restore works with this configuration).

The report names entity TYPES and locations only. Exit codes: 0 pass, 1 a check failed,
2 the configuration cannot start or the checks could not run.

Forbidden: calling a real provider. Printing values or keys. Using the gateway's own key
check (api/proxy.py) to decide: verify must see what the gateway missed.
"""

import asyncio
import json
import re
import secrets
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from pydantic import SecretStr, ValidationError

from antifaz.api.app import create_app
from antifaz.config import Settings, UnsafeConfigError, check_safe_to_start
from antifaz.detect.types import EntityType
from antifaz.policy import DEFAULT_POLICY, Action, Policy


@dataclass(frozen=True)
class Plant:
    type: EntityType
    value: str


# Synthetic values with valid check digits (the same ones the test suite uses).
PLANTS = (
    Plant(EntityType.ES_DNI, "12345678Z"),
    Plant(EntityType.ES_NIE, "X1234567L"),
    Plant(EntityType.IBAN, "ES91 2100 0418 4502 0005 1332"),
    Plant(EntityType.EMAIL, "ana.garcia@example.com"),
    Plant(EntityType.PHONE, "612 345 678"),
    Plant(EntityType.CREDIT_CARD, "4111 1111 1111 1111"),
)
PLANTED_TEXT = (
    "Cliente de prueba: DNI {}, NIE {}, IBAN {}, correo {}, teléfono {}, tarjeta {}."
).format(*(plant.value for plant in PLANTS))

Route = Literal["openai", "anthropic", "count_tokens"]
PATHS: dict[Route, str] = {
    "openai": "/v1/chat/completions",
    "anthropic": "/v1/messages",
    "count_tokens": "/v1/messages/count_tokens",
}
MODEL = "antifaz-verify"
ECHO_HEADERS_MODEL = "antifaz-verify-echo-headers"
VERIFY_HOST = "antifaz-verify.invalid"
# An answer that repeats a configured key is dropped with this status (ADR-0015).
ECHO_STATUS = 502
FAKE_UPSTREAM = "https://upstream.antifaz-verify.invalid"
_PLACEHOLDER = re.compile(r"\[\[[A-Z][A-Z0-9_]*_\d+\]\]")
_PIECE = 7  # streamed answers arrive in pieces this long, so placeholders are cut


@dataclass(frozen=True)
class Probe:
    route: Route
    location: str
    body: dict[str, Any]
    stream: bool = False
    planted: bool = True  # False: the probe only checks that keys do not come back


def _openai(location: str, messages: list[Any], *, stream: bool = False) -> Probe:
    body = {"model": MODEL, "messages": messages, "stream": stream}
    name = f"openai chat: {location}" + (", streaming" if stream else "")
    return Probe("openai", name, body, stream)


def _anthropic(
    location: str, messages: list[Any], *, system: Any = None, stream: bool = False
) -> Probe:
    body: dict[str, Any] = {"model": MODEL, "max_tokens": 64, "messages": messages}
    if system is not None:
        body["system"] = system
    if stream:
        body["stream"] = True
    return Probe(
        "anthropic", f"anthropic messages: {location}" + (", streaming" * stream), body, stream
    )


def _openai_tool_turn(arguments: str) -> dict[str, Any]:
    call = {
        "id": "call_verify_1",
        "type": "function",
        "function": {"name": "buscar_cliente", "arguments": arguments},
    }
    return {"role": "assistant", "content": None, "tool_calls": [call]}


def _anthropic_tool_turn(tool_input: dict[str, Any]) -> dict[str, Any]:
    block = {
        "type": "tool_use",
        "id": "toolu_verify_1",
        "name": "buscar_cliente",
        "input": tool_input,
    }
    return {"role": "assistant", "content": [block]}


def _anthropic_tool_result(content: Any) -> dict[str, Any]:
    block = {"type": "tool_result", "tool_use_id": "toolu_verify_1", "content": content}
    return {"role": "user", "content": [block]}


def probes() -> list[Probe]:
    """Every place a client can put text, on both endpoints, with and without streaming."""
    text = PLANTED_TEXT
    hello = {"role": "user", "content": "Hola"}
    tool_args = json.dumps({"datos": text}, ensure_ascii=False)
    openai_args = [
        hello,
        _openai_tool_turn(tool_args),
        {"role": "tool", "tool_call_id": "call_verify_1", "content": "ok"},
    ]
    openai_result = [
        hello,
        _openai_tool_turn("{}"),
        {"role": "tool", "tool_call_id": "call_verify_1", "content": text},
    ]
    anthropic_input = [hello, _anthropic_tool_turn({"datos": text}), _anthropic_tool_result("ok")]
    anthropic_result = [
        hello,
        _anthropic_tool_turn({}),
        _anthropic_tool_result([{"type": "text", "text": text}]),
    ]
    user = [{"role": "user", "content": text}]
    user_block = [{"role": "user", "content": [{"type": "text", "text": text}]}]
    return [
        _openai("system message", [{"role": "system", "content": text}, hello]),
        _openai("user message", user),
        _openai("user content part", user_block),
        _openai("tool call arguments", openai_args),
        _openai("tool result", openai_result),
        _openai("user message", user, stream=True),
        _openai("tool call arguments", openai_args, stream=True),
        _anthropic("system prompt", [hello], system=text),
        _anthropic("system blocks", [hello], system=[{"type": "text", "text": text}]),
        _anthropic("user message", user),
        _anthropic("user text block", user_block),
        _anthropic("tool_use input", anthropic_input),
        _anthropic("tool_result", anthropic_result),
        _anthropic("user message", user, stream=True),
        _anthropic("tool_result", anthropic_result, stream=True),
        Probe(
            "count_tokens",
            "anthropic count_tokens: user message",
            {"model": MODEL, "messages": user},
        ),
        Probe(
            "openai",
            "openai chat: provider echoes its headers",
            {"model": ECHO_HEADERS_MODEL, "messages": [hello]},
            planted=False,
        ),
        Probe(
            "anthropic",
            "anthropic messages: provider echoes its headers",
            {"model": ECHO_HEADERS_MODEL, "max_tokens": 64, "messages": [hello]},
            planted=False,
        ),
    ]


# --- The fake provider -----------------------------------------------------------------------


def _pieces(text: str) -> list[str]:
    return [text[i : i + _PIECE] for i in range(0, len(text), _PIECE)] or [""]


def _data(payload: Any, event: str | None = None) -> str:
    head = f"event: {event}\n" if event else ""
    return f"{head}data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _openai_answer(echo: str, stream: bool) -> httpx.Response:
    arguments = json.dumps({"echo": echo})
    call = {
        "index": 0,
        "id": "call_verify_2",
        "type": "function",
        "function": {"name": "echo", "arguments": arguments},
    }
    head = {
        "id": "chatcmpl-verify",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": MODEL,
    }
    if not stream:
        message = {"role": "assistant", "content": echo, "tool_calls": [call]}
        choice = {"index": 0, "message": message, "finish_reason": "tool_calls"}
        return httpx.Response(200, json={**head, "object": "chat.completion", "choices": [choice]})

    def chunk(delta: dict[str, Any], finish: str | None = None) -> str:
        return _data({**head, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]})

    events = [chunk({"role": "assistant", "content": ""})]
    events += [chunk({"content": piece}) for piece in _pieces(echo)]
    events.append(chunk({"tool_calls": [{**call, "function": {"name": "echo", "arguments": ""}}]}))
    events += [
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": piece}}]})
        for piece in _pieces(arguments)
    ]
    events += [chunk({}, "tool_calls"), "data: [DONE]\n\n"]
    return _event_stream(events)


def _anthropic_answer(echo: str, stream: bool) -> httpx.Response:
    usage = {"input_tokens": 1, "output_tokens": 1}
    message = {
        "id": "msg_verify",
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": usage,
    }
    tool = {"type": "tool_use", "id": "toolu_verify_2", "name": "echo"}
    if not stream:
        content = [{"type": "text", "text": echo}, {**tool, "input": {"echo": echo}}]
        return httpx.Response(200, json={**message, "content": content})

    def event(payload: dict[str, Any]) -> str:
        return _data(payload, payload["type"])

    def delta(index: int, body: dict[str, Any]) -> str:
        return event({"type": "content_block_delta", "index": index, "delta": body})

    start: dict[str, Any] = {**message, "content": [], "stop_reason": None}
    events = [event({"type": "message_start", "message": start})]
    events.append(
        event(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            }
        )
    )
    events += [delta(0, {"type": "text_delta", "text": piece}) for piece in _pieces(echo)]
    events.append(event({"type": "content_block_stop", "index": 0}))
    events.append(
        event({"type": "content_block_start", "index": 1, "content_block": {**tool, "input": {}}})
    )
    events += [
        delta(1, {"type": "input_json_delta", "partial_json": piece})
        for piece in _pieces(json.dumps({"echo": echo}))
    ]
    events.append(event({"type": "content_block_stop", "index": 1}))
    events.append(
        event(
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use"},
                "usage": {"output_tokens": 1},
            }
        )
    )
    events.append(event({"type": "message_stop"}))
    return _event_stream(events)


def _event_stream(events: Sequence[str]) -> httpx.Response:
    headers = {"content-type": "text/event-stream"}
    return httpx.Response(200, headers=headers, content="".join(events).encode("utf-8"))


class FakeProvider:
    """Records every request and answers with the placeholders it received (never a value)."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        text = request.content.decode("utf-8", "replace")
        body = json.loads(text)
        if body.get("model") == ECHO_HEADERS_MODEL:
            return httpx.Response(200, json={"echo": dict(request.headers)})
        if request.url.path.endswith("/count_tokens"):
            return httpx.Response(200, json={"input_tokens": 1})
        echo = " ".join(dict.fromkeys(_PLACEHOLDER.findall(text)))
        stream = body.get("stream") is True
        if request.url.path.endswith("/messages"):
            return _anthropic_answer(echo, stream)
        return _openai_answer(echo, stream)


# --- Checks ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Problem:
    kind: Literal[
        "leak",
        "gateway_key",
        "key_in_answer",
        "no_upstream",
        "blocked",
        "error",
        "echo_status",
        "not_restored",
    ]
    location: str
    entity: EntityType | None = None
    status: int | None = None


def _lines(problems: Sequence[Problem]) -> list[str]:
    """One line per kind of problem and location, with the entity types it concerns."""
    groups: dict[tuple[str, str, int | None], list[str]] = {}
    for problem in problems:
        entities = groups.setdefault((problem.kind, problem.location, problem.status), [])
        if problem.entity is not None:
            entities.append(problem.entity)
    lines = []
    for (kind, location, status), entities in groups.items():
        types = ", ".join(entities)
        what = {
            "leak": f"LEAK {types} reached the provider",
            "gateway_key": "LEAK the Antifaz key reached the provider",
            "key_in_answer": "KEY a configured key came back in the answer",
            "blocked": "BLOCKED the gateway refused the request (a planted value was not masked)",
            "no_upstream": "NO UPSTREAM the request did not reach the provider exactly once",
            "error": f"ERROR the gateway answered HTTP {status}",
            "echo_status": f"ERROR the gateway answered HTTP {status}, expected {ECHO_STATUS}",
            "not_restored": f"RESTORE {types} did not come back in the answer",
        }[kind]
        lines.append(f"{what} -- {location}")
    return lines


def _compact(text: str) -> str:
    return "".join(char for char in text.casefold() if char.isalnum())


def _strings(node: Any) -> Iterator[str]:
    if isinstance(node, str):
        yield node
        if node.lstrip()[:1] in ("{", "["):  # JSON inside a string: tool arguments
            yield from _json_strings(node)
    elif isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from _strings(item)


def _json_strings(text: str) -> Iterator[str]:
    try:
        parsed = json.loads(text)
    except (ValueError, RecursionError):
        return
    yield from _strings(parsed)


def _texts(raw: bytes) -> list[str]:
    """The raw text, and every decoded string of it (JSON, or the data of each SSE event)."""
    text = raw.decode("utf-8", "replace")
    out = [text, *_json_strings(text)]
    for line in text.splitlines():
        if line.startswith("data:"):
            out.extend(_json_strings(line[5:].strip()))
    return out


def _request_texts(requests: Sequence[httpx.Request]) -> list[str]:
    out: list[str] = []
    for request in requests:
        out.append(str(request.url))
        out.extend(f"{name}: {value}" for name, value in request.headers.items())
        out.extend(_texts(request.content))
    return out


def _holds(texts: Sequence[str], value: str) -> bool:
    """`value` as it is, or in any spacing or case (the planted values are 9+ characters)."""
    compact = _compact(value)
    return any(value in text or compact in _compact(text) for text in texts)


def _check(
    probe: Probe,
    answer: httpx.Response,
    sent: Sequence[httpx.Request],
    gateway_key: str,
    keys: Sequence[str],
    hidden: Sequence[Plant],
) -> list[Problem]:
    found: list[Problem] = []
    sent_texts = _request_texts(sent)
    found += [
        Problem("leak", probe.location, p.type) for p in hidden if _holds(sent_texts, p.value)
    ]
    if any(gateway_key in text for text in sent_texts):
        found.append(Problem("gateway_key", probe.location))
    answer_texts = _texts(answer.content) + [f"{k}: {v}" for k, v in answer.headers.items()]
    if any(key in text for text in answer_texts for key in keys):
        found.append(Problem("key_in_answer", probe.location))
    if len(sent) != 1:  # every probe is meant for the provider: otherwise nothing was proved
        found.append(Problem("no_upstream", probe.location))
    if not probe.planted:
        if answer.status_code != ECHO_STATUS:
            found.append(Problem("echo_status", probe.location, status=answer.status_code))
        return found
    if answer.status_code != 200:
        blocked = "antifaz_blocked" in answer.text
        found.append(
            Problem("blocked" if blocked else "error", probe.location, status=answer.status_code)
        )
    elif probe.route != "count_tokens":  # count_tokens only answers numbers
        found += [
            Problem("not_restored", probe.location, p.type)
            for p in hidden
            if not any(p.value in text for text in answer_texts)
        ]
    return found


def _headers(route: Route, gateway_key: str) -> dict[str, str]:
    if route == "openai":
        return {"Authorization": f"Bearer {gateway_key}"}
    return {"x-api-key": gateway_key, "anthropic-version": "2023-06-01"}


@dataclass(frozen=True)
class Report:
    requests: int
    problems: list[Problem]
    allowed: list[EntityType]  # planted types the policy lets through on purpose
    too_large: int = 0  # probes refused with 413 because of ANTIFAZ_MAX_BODY_BYTES


async def run_checks(settings: Settings, policy: Policy) -> Report:
    """Run every probe through an in-process gateway built from `settings`."""
    gateway_key = settings.antifaz_api_key.get_secret_value() if settings.antifaz_api_key else ""
    # Canaries for both provider keys and the panel token (invariant 13): the user's own
    # values never take part, and none of these may come back to the client.
    fake_keys = {
        name: SecretStr(f"verify-canary-{secrets.token_hex(16)}") for name in ("o", "a", "t")
    }
    run_settings = settings.model_copy(
        update={
            "openai_base_url": f"{FAKE_UPSTREAM}/v1",
            "openai_api_key": fake_keys["o"],
            "anthropic_base_url": FAKE_UPSTREAM,
            "anthropic_api_key": fake_keys["a"],
            "admin_token": fake_keys["t"],
            "allowed_hosts": [*settings.allowed_hosts, VERIFY_HOST],
            "log_level": "WARNING",
        }
    )
    keys = [gateway_key, *(key.get_secret_value() for key in fake_keys.values())]
    hidden = [plant for plant in PLANTS if policy.action_for(plant.type) is not Action.ALLOW]
    allowed = [plant.type for plant in PLANTS if plant not in hidden]
    provider = FakeProvider()
    problems: list[Problem] = []
    too_large = 0
    battery = probes()
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
        app = create_app(run_settings, http_client=upstream, policy=policy)
        # httpx.ASGITransport does not run the lifespan: run it here, as a server would.
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=f"http://{VERIFY_HOST}"
            ) as client,
        ):
            for probe in battery:
                before = len(provider.requests)
                answer = await client.post(
                    PATHS[probe.route], json=probe.body, headers=_headers(probe.route, gateway_key)
                )
                if answer.status_code == 413 and len(provider.requests) == before:
                    too_large += 1  # the user's body limit, not a privacy problem
                    continue
                sent = provider.requests[before:]
                problems += _check(probe, answer, sent, gateway_key, keys, hidden)
    return Report(len(battery), problems, allowed, too_large)


def _print_report(report: Report) -> None:
    types = ", ".join(plant.type for plant in PLANTS)
    lines = []
    if report.problems:
        found = _lines(report.problems)
        lines.append(f"antifaz verify: FAIL ({len(found)} problems)")
        lines += [f"  {line}" for line in found]
    else:
        lines += [
            "antifaz verify: PASS",
            f"  {report.requests} requests to /v1/chat/completions, /v1/messages and "
            "count_tokens, with and without streaming",
            f"  planted: {types}",
            "  none reached the fake provider, no key came back and every value was restored",
        ]
    lines += [
        f"  note: {entity} is allowed by the policy, so it was not checked"
        for entity in report.allowed
    ]
    lines.append("  no provider was called")
    print("\n".join(lines))


def _print_too_large(report: Report) -> None:
    print(
        f"antifaz verify: could not verify: {report.too_large} of {report.requests} test "
        "requests are larger than ANTIFAZ_MAX_BODY_BYTES (HTTP 413); raise it and run again\n"
        "  this is not a privacy failure: those requests were refused before reaching the "
        "provider",
        file=sys.stderr,
    )


def verify(settings: Settings) -> int:
    """Check `settings` and print the report. Never prints values or keys."""
    try:
        check_safe_to_start(settings)
    except UnsafeConfigError as error:  # its message names the variable, never the value
        print(f"antifaz verify: the gateway would not start: {error}", file=sys.stderr)
        return 2
    policy = DEFAULT_POLICY  # the one the server uses (create_app's default)
    failed = False
    try:
        report = asyncio.run(run_checks(settings, policy))
    except Exception:  # its message could hold a value or a key: never shown
        failed = True
    if failed:
        print("antifaz verify: the checks could not run", file=sys.stderr)
        return 2
    if report.problems:
        _print_report(report)
        if report.too_large:
            _print_too_large(report)
        return 1
    if report.too_large:
        _print_too_large(report)
        return 2
    _print_report(report)
    return 0


def verify_from_environment() -> int:
    """`antifaz verify`: the settings from ANTIFAZ_* variables and .env."""
    try:
        settings = Settings()
    except ValidationError as error:
        # Only the names of the fields: the error itself repeats the values it got.
        fields = sorted({str(detail["loc"][0]) for detail in error.errors() if detail["loc"]})
        print(
            "antifaz verify: cannot read the configuration; check " + ", ".join(fields),
            file=sys.stderr,
        )
        return 2
    return verify(settings)


__all__ = ["PLANTED_TEXT", "PLANTS", "FakeProvider", "Probe", "probes", "run_checks", "verify"]
