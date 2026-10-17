"""Fixtures of the panel's real-browser checks (PR 11c-2a). Only with --panel (`make panel-check`).

Playwright is imported inside the fixtures, never at the top: pytest loads this file even when
it is given `tests/browser` without --panel, and then nothing here may need the `panel` group.

- The whole session runs without any ANTIFAZ_*, OPENAI_* or ANTHROPIC_* variable and with
  forbid_network() (tests/conftest.py): this Python process may only open loopback
  connections. Playwright talks to its driver over pipes, so it is not affected.
- `browser_name` comes from --panel-browser (default chromium, firefox, webkit) and every test
  that uses a browser runs once per browser. One browser process per engine for the session.
- `server`: a new gateway per test (tests/browser/server.py), stopped afterwards; no provider
  may have been called. A new context per test (`tab`, or `new_tab(...)` for several).
- On a failure, a screenshot and a redacted text log go to test-results/panel-browser/ (the CI
  artefact). Never a trace, a video or a HAR.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from tests.browser.server import PanelServer
from tests.conftest import forbid_network, panel_browsers

if TYPE_CHECKING:
    from playwright.sync_api import Browser, Playwright

    from tests.browser.harness import NewTab, Tab

ARTIFACTS = Path(__file__).resolve().parents[2] / "test-results" / "panel-browser"
ISOLATED_PREFIXES = ("ANTIFAZ_", "OPENAI_", "ANTHROPIC_")
FAILED = pytest.StashKey[str]()


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "browser_name" in metafunc.fixturenames:
        metafunc.parametrize("browser_name", panel_browsers(metafunc.config), scope="session")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Any:
    report = yield
    if report.when == "call" and report.failed:
        item.stash[FAILED] = report.longreprtext
    return report


@pytest.fixture(scope="session")
def isolated() -> Iterator[None]:
    with pytest.MonkeyPatch.context() as patch:
        for name in list(os.environ):
            if name.upper().startswith(ISOLATED_PREFIXES):
                patch.delenv(name)
        forbid_network(patch)
        yield


@pytest.fixture(scope="session")
def playwright(isolated: None) -> Iterator[Playwright]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as driver:
        yield driver


@pytest.fixture(scope="session")
def browser(playwright: Playwright, browser_name: str) -> Iterator[Browser]:
    launched = getattr(playwright, browser_name).launch(headless=True)
    yield launched
    launched.close()


@pytest.fixture
def server(isolated: None) -> Iterator[PanelServer]:
    gateway = PanelServer()
    gateway.start()
    yield gateway
    gateway.stop()
    assert gateway.provider_calls == [], "a browser test reached a provider"


@pytest.fixture
def new_tab(
    request: pytest.FixtureRequest, browser: Browser, server: PanelServer
) -> Iterator[NewTab]:
    from tests.browser.harness import Tab

    tabs: list[Tab] = []

    def make(*, bypass_csp: bool = False, color_scheme: str = "light", width: int = 1280) -> Tab:
        tab = Tab(
            browser, server.origin, bypass_csp=bypass_csp, color_scheme=color_scheme, width=width
        )
        tabs.append(tab)
        return tab

    yield make
    failure = request.node.stash.get(FAILED, None)
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.nodeid)[-150:]
    for number, tab in enumerate(tabs):
        if failure is not None:
            tab.save_failure(ARTIFACTS, f"{stem}-{number}", server.throwaway_tokens, failure)
        tab.close()
    blocked = [url for tab in tabs for url in tab.blocked]
    assert blocked == [], "requests outside the test gateway were aborted"


@pytest.fixture
def tab(new_tab: NewTab) -> Tab:
    return new_tab()
