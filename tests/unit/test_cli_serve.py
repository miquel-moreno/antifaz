"""`antifaz serve` (issue 42, ADR-0017): the image's default command runs uvicorn exactly as the
old ENTRYPOINT did. The settings are pinned here; a change must be made on purpose.
"""

import inspect
import os
import sys
from typing import Any

import pytest
import uvicorn

from antifaz.cli import main, serve

# The ENTRYPOINT of the image up to v0.1.0, argument by argument.
OLD_ENTRYPOINT = [
    "--factory",
    "antifaz.api.app:create_app",
    "--host",
    "0.0.0.0",  # noqa: S104 - inside the container; Compose publishes it on 127.0.0.1 only
    "--port",
    "8000",
    "--no-access-log",
    "--no-proxy-headers",
    "--no-server-header",
    "--timeout-graceful-shutdown",
    "20",
]

# What the uvicorn command line passes that `uvicorn.run` would default differently, and why
# it makes no difference (or is stricter) for Antifaz.
ALLOWED_DIFFERENCES = {
    # The CLI adds the current folder to sys.path; serve imports only the installed package.
    "app_dir",
    # No extra headers either way ([] from the CLI, None from run()).
    "headers",
}


def _capture_run(monkeypatch: pytest.MonkeyPatch, target: Any) -> list[tuple[Any, dict[str, Any]]]:
    calls: list[tuple[Any, dict[str, Any]]] = []

    def fake_run(app: Any, **kwargs: Any) -> None:
        calls.append((app, kwargs))

    monkeypatch.setattr(target, "run", fake_run)
    return calls


def test_serve_settings_are_pinned() -> None:
    assert serve.APP == "antifaz.api.app:create_app"
    assert serve.OPTIONS == {
        "factory": True,
        "host": "0.0.0.0",  # noqa: S104 - see OLD_ENTRYPOINT
        "port": 8000,
        "access_log": False,
        "proxy_headers": False,
        "server_header": False,
        "timeout_graceful_shutdown": 20,
    }


def test_serve_runs_uvicorn_with_the_pinned_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture_run(monkeypatch, uvicorn)
    assert main(["serve"]) == 0
    assert calls == [("antifaz.api.app:create_app", serve.OPTIONS)]


def test_serve_is_the_same_server_as_the_old_entrypoint(monkeypatch: pytest.MonkeyPatch) -> None:
    defaults = {
        name: p.default
        for name, p in inspect.signature(uvicorn.run).parameters.items()
        if p.default is not inspect.Parameter.empty
    }
    # What the uvicorn command line would have run with the old arguments (UVICORN_* variables
    # cleared: they could change it), against what serve runs, default by default.
    for name in [n for n in os.environ if n.startswith("UVICORN_")]:
        monkeypatch.delenv(name)
    uvicorn_main = sys.modules["uvicorn.main"]
    old_calls = _capture_run(monkeypatch, uvicorn_main)
    uvicorn_main.main.main(OLD_ENTRYPOINT, prog_name="uvicorn", standalone_mode=False)
    ((old_app, old_kwargs),) = old_calls

    new_calls = _capture_run(monkeypatch, uvicorn)
    assert main(["serve"]) == 0
    ((new_app, new_kwargs),) = new_calls

    assert new_app == old_app
    assert set(new_kwargs) <= set(defaults)
    effective = defaults | new_kwargs
    different = {k for k, v in old_kwargs.items() if effective[k] != v}
    assert different == ALLOWED_DIFFERENCES, different


def test_serve_takes_no_options(capsys: pytest.CaptureFixture[str]) -> None:
    # Host, port and the rest are fixed: nothing from the command line can open them up.
    with pytest.raises(SystemExit) as info:
        main(["serve", "--port", "9000"])
    assert info.value.code == 2
    assert "9000" not in capsys.readouterr().err


def test_serve_does_not_read_uvicorn_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    # The old uvicorn command line read UVICORN_* (e.g. UVICORN_FORWARDED_ALLOW_IPS) from the
    # environment, and so from .env; serve passes its settings to uvicorn.run, which does not.
    monkeypatch.setenv("UVICORN_PROXY_HEADERS", "true")
    monkeypatch.setenv("UVICORN_HOST", "127.0.0.2")
    calls = _capture_run(monkeypatch, uvicorn)
    assert main(["serve"]) == 0
    assert calls == [("antifaz.api.app:create_app", serve.OPTIONS)]


def test_help_lists_serve(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        main(["--help"])
    assert info.value.code == 0
    assert "serve" in capsys.readouterr().out
