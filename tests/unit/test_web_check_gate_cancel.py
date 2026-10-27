"""Review 5 of 11e-4: a cancelled check gives the gate back (issue 53, ADR-0018).

The route POST /panel/api/status/check ends the gate in a `finally`. A client that goes away
cancels the request's task: the runner is cancelled mid-await (CancelledError is a
BaseException, not an Exception). The gate must be given back all the same, so the next press
after the cooldown runs. The route is called directly, with the context the door would set,
under a cancel scope; the runner awaits anyio.sleep_forever(). If the `finally` became
`except Exception`, the gate would stay taken and this test fails.
"""

import hashlib
from typing import Any

import anyio
from starlette.requests import Request

from antifaz.web.assets import load_assets
from antifaz.web.check_gate import CheckGate
from antifaz.web.cookies import session_scheme
from antifaz.web.door import CONTEXT_KEY, PanelContext, PanelState
from antifaz.web.login_limit import FailureLimiter
from antifaz.web.routes import build_router
from antifaz.web.sessions import MemorySessionStore
from antifaz.web.status import NER_OFF, StatusFacts
from antifaz.web.try_limit import TryLimiter

PATH = "/panel/api/status/check"
RESULTS = [
    {"id": "openai", "result": "ok", "http_status": None},
    {"id": "anthropic", "result": "no_key", "http_status": None},
]


class _Clock:
    now = 100.0

    def __call__(self) -> float:
        return self.now


class _Runner:
    """Hangs (until cancelled) while `hang` is set, then answers RESULTS."""

    def __init__(self) -> None:
        self.hang = True
        self.started = 0

    async def __call__(self) -> list[dict[str, Any]]:
        self.started += 1
        if self.hang:
            await anyio.sleep_forever()
        return RESULTS


async def _no_try(text: str, lang: str, ner: bool) -> dict[str, Any]:
    raise AssertionError("not called here")


def _endpoint_and_request(state: PanelState) -> tuple[Any, Request]:
    (route,) = [r for r in build_router(state).routes if getattr(r, "path", "") == PATH]
    session_id, session = state.sessions.create(session_scheme("https"))
    context = PanelContext("https", "antifaz.example", session_id, session)
    scope = {
        "type": "http",
        "method": "POST",
        "path": PATH,
        "headers": [],
        "query_string": b"",
        "state": {CONTEXT_KEY: context},
    }
    return route.endpoint, Request(scope)  # type: ignore[attr-defined]


async def test_a_cancelled_check_gives_the_gate_back() -> None:
    clock = _Clock()
    runner = _Runner()
    gate = CheckGate(clock=clock)
    state = PanelState(
        token_fingerprint=hashlib.sha256(b"x").digest(),
        trusted_proxies=(),
        sessions=MemorySessionStore(),
        limiter=FailureLimiter(),
        assets=load_assets(),
        status=StatusFacts("0.0.0", (), 1, 0, 0, lambda: NER_OFF),
        try_runner=_no_try,
        try_limiter=TryLimiter(),
        check_runner=runner,
        check_gate=gate,
    )
    endpoint, request = _endpoint_and_request(state)

    with anyio.move_on_after(0.2) as scope:
        await endpoint(request)

    assert scope.cancelled_caught  # the runner really hung and was cancelled
    assert runner.started == 1
    assert not gate.running
    runner.hang = False
    clock.now += 30  # the cooldown counts from the end of the cancelled check
    with anyio.fail_after(10):  # a hang guard
        answer = await endpoint(request)
    assert answer.status_code == 200
    assert runner.started == 2
    assert not gate.running
