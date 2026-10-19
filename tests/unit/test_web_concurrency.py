"""The panel's session store and login limiter have no locks (issue 53, ADR-0018, PR 11b-2).

They are correct only because every panel route is `async def` on the one event loop and
nothing awaits in the middle of their read-modify-write. These tests keep it that way:

1. Every function and method of web/sessions.py and web/login_limit.py is a plain `def` and
   those modules hold no `await`: no call into them can be suspended half-way.
2. Every route endpoint of web/routes.py is a coroutine function (`async def`), so FastAPI
   never runs one in its thread pool, where two could run at the same time.
3. In the login, no await, async for or async with comes after the limiter check (by line):
   from `limiter.allowed(...)` to the response, checking, recording a failure and creating the
   session happen without the coroutine ever being suspended.
4. The logout (11b-2b) has no await at all: it closes the session and answers in one go.
5. In the door, nothing awaits between looking the session up (peek) and counting the use
   (touch): no other request can close it in between and see it revived.
"""

import ast
import hashlib
import inspect
from pathlib import Path

import pytest

import antifaz.web
from antifaz.web.assets import load_assets
from antifaz.web.door import PanelState
from antifaz.web.login_limit import FailureLimiter
from antifaz.web.routes import build_router
from antifaz.web.sessions import MemorySessionStore
from antifaz.web.status import NER_OFF, StatusFacts

WEB = Path(antifaz.web.__file__).parent
LOCK_FREE = ("sessions.py", "login_limit.py", "try_limit.py")


def _tree(name: str) -> ast.Module:
    path = WEB / name
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_the_lock_free_modules_have_no_async_function() -> None:
    for name in LOCK_FREE:
        tree = _tree(name)
        functions = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        assert functions, name  # not an empty pass
        asynchronous = [n.name for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)]
        assert asynchronous == [], (name, asynchronous)


def test_the_lock_free_modules_never_await() -> None:
    for name in LOCK_FREE:
        tree = _tree(name)
        awaits = [
            n for n in ast.walk(tree) if isinstance(n, ast.Await | ast.AsyncFor | ast.AsyncWith)
        ]
        assert awaits == [], name


def test_every_route_function_in_routes_py_is_async() -> None:
    tree = _tree("routes.py")
    decorated = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.decorator_list
    ]
    assert decorated, "no route found: the check would pass without checking anything"
    for node in decorated:
        assert isinstance(node, ast.AsyncFunctionDef), node.name


def test_every_registered_panel_endpoint_is_a_coroutine_function() -> None:
    state = PanelState(
        token_fingerprint=hashlib.sha256(b"x").digest(),
        trusted_proxies=(),
        sessions=MemorySessionStore(),
        limiter=FailureLimiter(),
        assets=load_assets(),
        status=StatusFacts("0.0.0", (), 1, 0, 0, lambda: NER_OFF),
    )
    routes = build_router(state).routes
    assert routes
    for route in routes:
        endpoint = route.endpoint  # type: ignore[attr-defined]
        assert inspect.iscoroutinefunction(endpoint), route


def test_the_checks_catch_what_they_forbid() -> None:
    bad = ast.parse("async def f():\n    await g()\n\n@r.get('/x')\ndef h():\n    pass\n")
    assert any(isinstance(n, ast.AsyncFunctionDef) for n in ast.walk(bad))
    assert any(isinstance(n, ast.Await) for n in ast.walk(bad))
    plain = [n for n in ast.walk(bad) if isinstance(n, ast.FunctionDef) and n.decorator_list]
    assert plain and not isinstance(plain[0], ast.AsyncFunctionDef)


# --- The login's critical section -----------------------------------------------------------

_ASYNC_NODES = (ast.Await, ast.AsyncFor, ast.AsyncWith)


def _awaits_after_the_limiter(tree: ast.Module, function: str = "login") -> list[int]:
    """Line numbers of every await-like node in `function` after its first `.allowed(` call.

    From checking the limiter to the response, nothing may suspend the coroutine: another
    login could then pass the same check before this one records its failure."""
    (node,) = [
        n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == function
    ]
    calls = [
        n.lineno
        for n in ast.walk(node)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "allowed"
    ]
    assert calls, "no limiter check found: the check would pass without checking anything"
    first = min(calls)
    return [n.lineno for n in ast.walk(node) if isinstance(n, _ASYNC_NODES) and n.lineno > first]


def test_the_login_never_awaits_between_the_limiter_and_the_response() -> None:
    assert _awaits_after_the_limiter(_tree("routes.py")) == []


def test_the_login_reads_its_body_before_the_limiter() -> None:
    (node,) = [
        n
        for n in ast.walk(_tree("routes.py"))
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "login"
    ]
    awaits = [n for n in ast.walk(node) if isinstance(n, _ASYNC_NODES)]
    assert awaits, "the body is read with an await before the limiter"


@pytest.mark.parametrize(
    "inserted",
    [
        "    await asyncio.sleep(0)\n",
        "    async for _ in stream():\n        pass\n",
        "    async with lock:\n        pass\n",
        "    if x:\n        y = [await z()]\n",
    ],
)
def test_the_critical_section_check_catches_an_inserted_await(inserted: str) -> None:
    source = (
        "async def login(request):\n"
        "    body = await read(request)\n"
        "    allowed, wait = state.limiter.allowed(address)\n"
        f"{inserted}"
        "    state.limiter.record_failure(address)\n"
    )

    assert _awaits_after_the_limiter(ast.parse(source)) != []
    clean = source.replace(inserted, "")
    assert _awaits_after_the_limiter(ast.parse(clean)) == []


# --- The logout (11b-2b) --------------------------------------------------------------------


def _async_nodes_in(tree: ast.Module, function: str) -> list[int]:
    (node,) = [
        n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == function
    ]
    return [n.lineno for n in ast.walk(node) if isinstance(n, _ASYNC_NODES)]


def test_the_logout_never_awaits() -> None:
    """Closing the session and answering happen in one go: no await, async for or async with."""
    assert _async_nodes_in(_tree("routes.py"), "logout") == []


def test_the_logout_check_catches_an_await() -> None:
    bad = ast.parse("async def logout(request):\n    await request.body()\n    return 1\n")

    assert _async_nodes_in(bad, "logout") == [2]


# --- The door: from peek() to touch() -------------------------------------------------------


def _awaits_between(tree: ast.Module, function: str, first: str, last: str) -> list[int]:
    """Await-like nodes in `function` on lines from the first `.first(` to the last `.last(`."""
    (node,) = [
        n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == function
    ]

    def lines(attribute: str) -> list[int]:
        return [
            n.lineno
            for n in ast.walk(node)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == attribute
        ]

    starts, ends = lines(first), lines(last)
    assert starts and ends, "no peek/touch found: the check would pass without checking anything"
    low, high = min(starts), max(ends)
    return [
        n.lineno for n in ast.walk(node) if isinstance(n, _ASYNC_NODES) and low < n.lineno < high
    ]


def test_the_door_never_awaits_between_peek_and_touch() -> None:
    assert _awaits_between(_tree("door.py"), "_handle", "peek", "touch") == []


def _store_calls(tree: ast.Module) -> list[str]:
    """Methods called on something named `sessions` (a local or an attribute)."""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            target = n.func.value
            name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", "")
            if name == "sessions":
                out.append(n.func.attr)
    return out


def test_the_door_never_refreshes_a_session_with_get() -> None:
    """get() counts a use before the checks; the door must peek, check, then touch."""
    calls = _store_calls(_tree("door.py"))

    assert "peek" in calls and "touch" in calls
    assert "get" not in calls
    assert "get" in _store_calls(ast.parse("x = self._state.sessions.get(i, s)"))


def test_the_peek_touch_check_catches_an_await() -> None:
    source = (
        "async def _handle(scope):\n"
        "    session = sessions.peek(i, s)\n"
        "    await send(x)\n"
        "    sessions.touch(session.fingerprint)\n"
    )

    assert _awaits_between(ast.parse(source), "_handle", "peek", "touch") == [3]
