"""One browser context on the test gateway (PR 11c-2a): what the browser tests look at.

A `Tab` is a new context (never shared between tests) with reduced motion, service workers
blocked, a browser locale (it sets Accept-Language, PR 11c-2b; en-US unless a test says
otherwise, never the system's), a pixel ratio and:

- a context route on every URL: a request to the gateway's own origin goes on (and to
  `extra_origin`, the separate OtherSite server of the cross-site tests, PR 11c-2b; never the
  gateway); anything else is aborted and recorded in `blocked` (the fixture fails the test
  if it is not empty);
- every request URL (`requests`), console error or warning (`console`), uncaught page error
  (`errors`) and failed request (`failed`); every console text too, to look for a token;
- an init script that records every `securitypolicyviolation` of every document
  (`csp_violations()`, read after a frame and a microtask); a canary test proves it works;
- `goto()` settles on `load` and `document.fonts.ready`, never on a fixed wait, and keeps the
  response, so a test can check the CSP header the browser really got (`csp_header()`);
- the colour scheme asked for, emulated again on the page after every `goto()` and checked
  there (fail-closed): Playwright's Firefox drops the context's `color_scheme` on the first
  navigation into a page with `Cross-Origin-Opener-Policy: same-origin` (the gateway's pages),
  so before this the "dark" checks ran light in Firefox. Once emulated on the page it holds
  through later navigations and reloads.

Secret values never reach a test: `cookie_names()` and `cookie_attributes()` drop the values,
and only this module reads the cookie jar (tests/unit/test_browser_harness_rules.py). The
values a tab saw (session cookies, the CSRF meta) are kept in `seen_secrets` for the redactor.

Failure artefacts (`save_failure`) are a screenshot and a text log with every known secret and
anything shaped like one replaced (tests/browser/redact.py). Never a trace, a video or a HAR:
they would hold the tokens and the cookies.
"""

import contextlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from axe_playwright_python.base import AXE_FILE_PATH
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import (
    Browser,
    ConsoleMessage,
    Error,
    Request,
    Response,
    Route,
    WebError,
)

from antifaz.web.headers import CONTENT_SECURITY_POLICY
from tests.browser.redact import redact

# Playwright's default is the system's locale: fixed here, so every test says its language.
DEFAULT_LOCALE = "en-US"
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
# One rendering frame and a microtask first: a violation event queued by the last change has
# been dispatched by then.
CSP_VIOLATIONS_JS = """async () => {
  await new Promise((done) => requestAnimationFrame(() => queueMicrotask(done)));
  return [...window.__antifazCsp];
}"""
DARK_JS = "() => matchMedia('(prefers-color-scheme: dark)').matches"
CSRF_META_JS = """() => {
  const meta = document.querySelector('meta[name="antifaz-csrf"]');
  return meta ? meta.content : null;
}"""


@dataclass(frozen=True)
class AxeReport:
    violations: str  # one line per rule, "" when there is none
    passes: int  # rules that passed: 0 would mean axe checked nothing
    incomplete: list[str]  # rules axe could not decide, for the failure message


class Tab:
    def __init__(
        self,
        browser: Browser,
        origin: str,
        *,
        bypass_csp: bool = False,
        color_scheme: str = "light",
        width: int = 1280,
        locale: str | None = None,
        scale: int = 1,
        height: int | None = None,
        extra_origin: str | None = None,
    ) -> None:
        self.origin = origin
        self.extra_origin = extra_origin
        self.requests: list[str] = []
        self.blocked: list[str] = []
        self.console: list[str] = []
        self.console_locations: list[str] = []  # the URL of each line of `console`
        self.console_texts: list[str] = []
        self.errors: list[str] = []
        self.failed: list[str] = []
        self.seen_secrets: set[str] = set()
        self.response: Response | None = None
        self.color_scheme = color_scheme
        self.context = browser.new_context(
            bypass_csp=bypass_csp,
            color_scheme=color_scheme,  # type: ignore[arg-type]
            reduced_motion="reduce",
            service_workers="block",
            viewport={"width": width, "height": height or (844 if width < 768 else 800)},
            device_scale_factor=scale,
            locale=locale or DEFAULT_LOCALE,
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
        extra = self.extra_origin
        if self._same_origin(url) or (extra and (url == extra or url.startswith(extra + "/"))):
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
            self.console_locations.append(str(message.location.get("url", "")))

    def _on_error(self, error: WebError) -> None:
        self.errors.append(str(error.error))

    def goto(self, path: str) -> None:
        """Open a path of the gateway and settle: load, then every font ready."""
        self.response = self.page.goto(self.origin + path, wait_until="load")
        self.page.emulate_media(color_scheme=self.color_scheme)  # type: ignore[arg-type]
        dark: bool = self.page.evaluate(DARK_JS)
        assert dark == (self.color_scheme == "dark"), "the page lost the requested colour scheme"
        self.page.evaluate(SETTLED_JS)
        self.remember_secrets()

    def csp_header(self) -> str | None:
        """The Content-Security-Policy of the last document `goto` opened (after redirects)."""
        return self.response.header_value("content-security-policy") if self.response else None

    def csp_violations(self) -> list[str]:
        violations: list[str] = self.page.evaluate(CSP_VIOLATIONS_JS)
        return violations

    def foreign_requests(self) -> list[str]:
        return [url for url in self.requests if not self._same_origin(url)]

    def serve(self, path: str, html: str, headers: dict[str, str]) -> None:
        """A same-origin HTML page answered by the browser itself, with these headers."""
        self.page.route(
            self.origin + path,
            lambda route: route.fulfill(
                status=200,
                headers={"Content-Type": "text/html; charset=utf-8", **headers},
                body=html,
            ),
        )

    def serve_canary(self, path: str, html: str) -> None:
        """A same-origin page with the panel's real CSP header."""
        self.serve(path, html, {"Content-Security-Policy": CONTENT_SECURITY_POLICY})

    def cookie_names(self) -> list[str]:
        """The names of the context's cookies, never their values."""
        return [cookie["name"] for cookie in self.context.cookies()]

    def cookie_attributes(self) -> list[dict[str, Any]]:
        """Every cookie of the context without its value."""
        return [
            {key: value for key, value in cookie.items() if key != "value"}
            for cookie in self.context.cookies()
        ]

    def remember_secrets(self) -> None:
        """Keep the cookie values and the CSRF meta of now, for the redactor."""
        with contextlib.suppress(Error):
            self.seen_secrets.update(cookie["value"] for cookie in self.context.cookies())
            meta = self.page.evaluate(CSRF_META_JS)
            if meta:
                self.seen_secrets.add(meta)

    def shows(self, secret: str) -> bool:
        """Whether `secret` is in the DOM, the address, a request URL or any console text."""
        places = [self.page.content(), self.page.url, *self.requests, *self.console_texts]
        return any(secret in place for place in places)

    def axe(self) -> AxeReport:
        """axe-core on the page, every result type."""
        results = Axe.from_file(AXE_FILE_PATH).run(self.page, options=None)
        return AxeReport(
            violations=results.generate_snapshot(),
            passes=len(results.response["passes"]),
            incomplete=sorted(rule["id"] for rule in results.response["incomplete"]),
        )

    def save_failure(self, folder: Path, stem: str, tokens: Iterable[str], report: str) -> None:
        """A screenshot and a redacted text log, for the CI artefact of a failed test."""
        folder.mkdir(parents=True, exist_ok=True)
        self.remember_secrets()
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
        log = redact("\n".join(lines), [*tokens, *self.seen_secrets])
        (folder / f"{stem}.log").write_text(log, encoding="utf-8")

    def close(self) -> None:
        self.context.close()


class NewTab(Protocol):
    def __call__(
        self,
        *,
        bypass_csp: bool = False,
        color_scheme: str = "light",
        width: int = 1280,
        locale: str | None = None,
        scale: int = 1,
        height: int | None = None,
        extra_origin: str | None = None,
    ) -> Tab: ...
