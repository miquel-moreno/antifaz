"""Fixtures of the panel's real-browser checks (PR 11c-2a). Only with --panel (`make panel-check`).

Playwright is imported inside the fixtures, never at the top: pytest loads this file even when
it is given `tests/browser` without --panel, and then nothing here may need the `panel` group.

- The whole session runs without any ANTIFAZ_*, OPENAI_*, ANTHROPIC_* or PLAYWRIGHT_* variable,
  without DEBUG, DEBUG_FILE, PWDEBUG or proxies (server.isolated_variable), and with
  forbid_network() (tests/conftest.py): this Python process may only open loopback
  connections. Playwright talks to its driver over pipes, so it is not affected. Chromium also
  gets host resolver rules that resolve nothing but 127.0.0.1.
- `browser_name` comes from --panel-browser (default chromium, firefox, webkit) and every test
  that uses a browser runs once per browser. One browser process per engine for the session.
- `server`: a new gateway per test (tests/browser/server.py), stopped afterwards; no provider
  may have been called. A test module changes what it has by overriding `server_options`
  (11c-3b: throwaway provider keys, a fake NER state). A new context per test (`tab`, or
  `new_tab(...)` for several).
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

from tests.browser.server import OtherSite, PanelServer, ServerOptions, isolated_variable
from tests.conftest import forbid_network, panel_browsers

if TYPE_CHECKING:
    from playwright.sync_api import Browser, Playwright

    from tests.browser.harness import NewTab, Tab

ARTIFACTS = Path(__file__).resolve().parents[2] / "test-results" / "panel-browser"
# Chromium resolves no name but 127.0.0.1 and localhost (loopback too: the second site of the
# cross-site tests, PR 11c-2b): an extra guard against browser-internal traffic (the context
# route already aborts every request the page makes elsewhere).
LAUNCH_ARGS = {
    "chromium": ["--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1 , EXCLUDE localhost"]
}
FAILED = pytest.StashKey[str]()
NOTES = pytest.StashKey[list[str]]()


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "browser_name" in metafunc.fixturenames:
        metafunc.parametrize("browser_name", panel_browsers(metafunc.config), scope="session")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Any:
    report = yield
    if report.when == "call" and report.failed:
        item.stash[FAILED] = report.longreprtext
    return report


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: pytest.Config) -> None:
    """The notes the tests left (e.g. the SameSite each browser stored), at the end of the log,
    whatever the output capture. Only attributes go there, never a value or a token."""
    notes = config.stash.get(NOTES, [])
    if notes:
        terminalreporter.section("panel browser notes")
        for note in notes:
            terminalreporter.write_line(note)


@pytest.fixture
def panel_notes(request: pytest.FixtureRequest) -> list[str]:
    """A line appended here is printed in the terminal summary."""
    return request.config.stash.setdefault(NOTES, [])


@pytest.fixture(scope="session")
def isolated() -> Iterator[None]:
    with pytest.MonkeyPatch.context() as patch:
        for name in list(os.environ):
            if isolated_variable(name):
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
    engine = getattr(playwright, browser_name)
    launched = engine.launch(headless=True, args=LAUNCH_ARGS.get(browser_name, []))
    yield launched
    launched.close()


@pytest.fixture
def server_options() -> ServerOptions:
    """The defaults: no provider key, the NER off. A test module overrides this fixture."""
    return ServerOptions()


@pytest.fixture
def server(
    request: pytest.FixtureRequest,
    isolated: None,
    panel_notes: list[str],
    server_options: ServerOptions,
) -> Iterator[PanelServer]:
    gateway = PanelServer(server_options)
    try:
        gateway.start()
        yield gateway
    finally:
        gateway.stop()  # socket, thread and HTTP client, also when start() failed
        if gateway.hit_shutdown_limit:
            # Visible in the terminal summary: a real hang must not hide behind the limit.
            panel_notes.append(f"graceful-shutdown limit hit: {request.node.nodeid}")
    assert gateway.provider_calls == [], "a browser test reached a provider"


@pytest.fixture(params=["localhost", "127.0.0.1"], ids=["cross-site", "same-site"])
def other_site(request: pytest.FixtureRequest, isolated: None) -> Iterator[OtherSite]:
    """Another origin on loopback (PR 11c-2b): named localhost (another site) or 127.0.0.1 on
    its own port (the same site, so the session cookie travels)."""
    site = OtherSite(request.param)
    try:
        site.start()
        yield site
    finally:
        site.stop()


@pytest.fixture
def new_tab(
    request: pytest.FixtureRequest, browser: Browser, server: PanelServer
) -> Iterator[NewTab]:
    from tests.browser.harness import Tab

    tabs: list[Tab] = []

    def make(
        *,
        bypass_csp: bool = False,
        color_scheme: str = "light",
        width: int = 1280,
        locale: str | None = None,
        scale: int = 1,
        height: int | None = None,
        extra_origin: str | None = None,
        forced_colors: str = "none",
        javascript: bool = True,
    ) -> Tab:
        tab = Tab(
            browser,
            server.origin,
            bypass_csp=bypass_csp,
            color_scheme=color_scheme,
            width=width,
            locale=locale,
            scale=scale,
            height=height,
            extra_origin=extra_origin,
            forced_colors=forced_colors,
            javascript=javascript,
        )
        tabs.append(tab)
        return tab

    yield make
    failure = request.node.stash.get(FAILED, None)
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.nodeid)[-150:]
    for number, tab in enumerate(tabs):
        tab.remember_secrets()  # this tab's cookie values and CSRF token, for the redactor
        if failure is not None:
            tab.save_failure(ARTIFACTS, f"{stem}-{number}", server.throwaway_tokens, failure)
        tab.close()
    blocked = [url for tab in tabs for url in tab.blocked]
    assert blocked == [], "requests outside the test gateway were aborted"


@pytest.fixture
def tab(new_tab: NewTab) -> Tab:
    return new_tab()
