"""One browser context on the test gateway (PR 11c-2a): what the browser tests look at.

A `Tab` is a new context (never shared between tests) with reduced motion, service workers
blocked and:

- a context route on every URL: a request to the gateway's own origin goes on; anything else
  is aborted and recorded in `blocked` (the fixture fails the test if it is not empty);
- every request URL (`requests`), console error or warning (`console`), uncaught page error
  (`errors`) and failed request (`failed`); every console text too, to look for a token;
- an init script that records every `securitypolicyviolation` of every document
  (`csp_violations()`); a canary test proves it works in each browser;
- `goto()` settles on `load` and `document.fonts.ready`, never on a fixed wait.

Failure artefacts (`save_failure`) are a screenshot and a text log with every token and
anything shaped like one replaced. Never a trace, a video or a HAR: they would hold the tokens
and the cookies.
"""

import contextlib
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from axe_playwright_python.base import AXE_FILE_PATH
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import (
    Browser,
    ConsoleMessage,
    Error,
    Request,
    Route,
    WebError,
)

from antifaz.web.headers import CONTENT_SECURITY_POLICY

# Hang guards only (a page that never loads), never a measure of speed.
TIMEOUT_MS = 30_000

CSP_RECORDER = """(() => {
  if (window.__antifazCsp) return;
  const seen = [];
  Object.defineProperty(window, '__antifazCsp', {value: seen});
  document.addEventListener('securitypolicyviolation',
    (e) => seen.push(e.violatedDirective + ' ' + e.blockedURI), true);
})();"""
SETTLED_JS = "() => document.fonts.ready.then(() => true)"
# Anything shaped like a token in a failure log: hex of 64 (the gateway's throwaway tokens)
# and token_urlsafe(32) (session ids and CSRF tokens).
TOKEN_SHAPES = re.compile(r"\b[0-9a-f]{64}\b|(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])")


def redact(text: str, tokens: Iterable[str]) -> str:
    for token in tokens:
        text = text.replace(token, "<redacted>")
    return TOKEN_SHAPES.sub("<redacted>", text)


class Tab:
    def __init__(
        self,
        browser: Browser,
        origin: str,
        *,
        bypass_csp: bool = False,
        color_scheme: str = "light",
        width: int = 1280,
    ) -> None:
        self.origin = origin
        self.requests: list[str] = []
        self.blocked: list[str] = []
        self.console: list[str] = []
        self.console_texts: list[str] = []
        self.errors: list[str] = []
        self.failed: list[str] = []
        self.context = browser.new_context(
            bypass_csp=bypass_csp,
            color_scheme=color_scheme,  # type: ignore[arg-type]
            reduced_motion="reduce",
            service_workers="block",
            viewport={"width": width, "height": 844 if width < 768 else 800},
        )
        self.context.set_default_timeout(TIMEOUT_MS)
        self.context.set_default_navigation_timeout(TIMEOUT_MS)
        self.context.add_init_script(CSP_RECORDER)
        self.context.route("**/*", self._only_the_gateway)
        self.context.on("request", self._on_request)
        self.context.on("requestfailed", self._on_failed)
        self.context.on("console", self._on_console)
        self.context.on("weberror", self._on_error)
        self.page = self.context.new_page()

    def _same_origin(self, url: str) -> bool:
        return url == self.origin or url.startswith(self.origin + "/")

    def _only_the_gateway(self, route: Route) -> None:
        url = route.request.url
        if self._same_origin(url):
            route.continue_()
        else:
            self.blocked.append(url)
            route.abort("blockedbyclient")

    def _on_request(self, request: Request) -> None:
        self.requests.append(request.url)

    def _on_failed(self, request: Request) -> None:
        self.failed.append(f"{request.method} {request.url}: {request.failure}")

    def _on_console(self, message: ConsoleMessage) -> None:
        self.console_texts.append(message.text)
        if message.type in ("error", "warning"):
            self.console.append(f"[{message.type}] {message.text}")

    def _on_error(self, error: WebError) -> None:
        self.errors.append(str(error.error))

    def goto(self, path: str) -> None:
        """Open a path of the gateway and settle: load, then every font ready."""
        self.page.goto(self.origin + path, wait_until="load")
        self.page.evaluate(SETTLED_JS)

    def csp_violations(self) -> list[str]:
        violations: list[str] = self.page.evaluate("() => [...window.__antifazCsp]")
        return violations

    def foreign_requests(self) -> list[str]:
        return [url for url in self.requests if not self._same_origin(url)]

    def serve_canary(self, path: str, html: str) -> None:
        """A same-origin page with the panel's real CSP header, answered by the browser."""
        self.page.route(
            self.origin + path,
            lambda route: route.fulfill(
                status=200,
                headers={
                    "Content-Type": "text/html; charset=utf-8",
                    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
                },
                body=html,
            ),
        )

    def shows(self, secret: str) -> bool:
        """Whether `secret` is in the DOM, the address, a request URL or any console text."""
        places = [self.page.content(), self.page.url, *self.requests, *self.console_texts]
        return any(secret in place for place in places)

    def axe(self) -> str:
        """axe-core's violations, one line per rule ("" when there is none)."""
        results = Axe.from_file(AXE_FILE_PATH).run(self.page)
        snapshot: str = results.generate_snapshot()
        return snapshot

    def save_failure(self, folder: Path, stem: str, tokens: Iterable[str], report: str) -> None:
        """A screenshot and a redacted text log, for the CI artefact of a failed test."""
        folder.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(Error):  # a closed or crashed page: the log still tells
            self.page.screenshot(path=folder / f"{stem}.png", full_page=True, timeout=TIMEOUT_MS)
        lines = [
            f"url: {self.page.url}",
            *(f"console: {line}" for line in self.console),
            *(f"page error: {line}" for line in self.errors),
            *(f"failed: {line}" for line in self.failed),
            *(f"blocked: {line}" for line in self.blocked),
            "",
            report,
        ]
        log = redact("\n".join(lines), tokens)
        (folder / f"{stem}.log").write_text(log, encoding="utf-8")

    def close(self) -> None:
        self.context.close()


class NewTab(Protocol):
    def __call__(
        self, *, bypass_csp: bool = False, color_scheme: str = "light", width: int = 1280
    ) -> Tab: ...
