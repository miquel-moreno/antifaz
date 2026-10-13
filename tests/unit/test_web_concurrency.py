"""The panel's session store and login limiter have no locks (issue 53, ADR-0018, PR 11b-2a).

They are correct only because every panel route is `async def` on the one event loop and
nothing awaits in the middle of their read-modify-write. These tests keep it that way:

1. Every function and method of web/sessions.py and web/login_limit.py is a plain `def` and
   those modules hold no `await`: no call into them can be suspended half-way.
2. Every route endpoint of web/routes.py is a coroutine function (`async def`), so FastAPI
   never runs one in its thread pool, where two could run at the same time.
"""

import ast
import hashlib
import inspect
from pathlib import Path

import antifaz.web
from antifaz.web.door import PanelState
from antifaz.web.login_limit import FailureLimiter
from antifaz.web.routes import build_router
from antifaz.web.sessions import MemorySessionStore

WEB = Path(antifaz.web.__file__).parent
LOCK_FREE = ("sessions.py", "login_limit.py")


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
