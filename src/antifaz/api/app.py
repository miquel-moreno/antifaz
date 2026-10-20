"""FastAPI application factory.

The server builds the app with `uvicorn --factory antifaz.api.app:create_app`: nothing is
built at import time, so a refused start (ADR-0015) is never an import side effect.
"""

import functools
import hashlib
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import anyio
import anyio.to_thread
import httpx
from fastapi import FastAPI
from fastapi.routing import iter_route_contexts
from fastapi.telemetry import TelemetryConfig
from starlette.types import ASGIApp

from antifaz import __version__
from antifaz.api import anthropic, health, models, openai, scan
from antifaz.api.errors import register_error_handlers
from antifaz.api.gate import CaseInsensitiveTrustedHost, GateMiddleware
from antifaz.api.middleware import request_id_middleware
from antifaz.api.streaming import StreamCounters
from antifaz.config import Settings, check_safe_to_start, get_settings
from antifaz.detect.ner.chunker import OVERLAP_TOKENS, WINDOW_TOKENS
from antifaz.detect.ner.engine import NerDetector
from antifaz.detect.ner.setup import ner_from_settings
from antifaz.detect.scan import Scanner
from antifaz.detect.scan import scan as scan_text
from antifaz.logging import configure_logging
from antifaz.mask import Detector
from antifaz.policy import DEFAULT_POLICY, Policy
from antifaz.providers.http import build_client

if TYPE_CHECKING:  # antifaz.web is imported only when the panel is on (_panel)
    from antifaz.web.status import UrlParts

# Threads that can mask at the same time (detection and waiting for the NER). A limiter of our
# own: masking never takes the threads of anyio's shared default limiter from other work.
MASK_THREADS = 8
# «Prueba un texto» (11d-a): one Try at a time, never queued (a second one is 503 try_busy), and
# taken BEFORE the mask limiter, so the panel holds at most one of the MASK_THREADS. With the NER
# on, the proxy goes first (NerPool.try_predict): a Try is 503 ner_busy at once if a proxy request
# is waiting for a worker, if no worker is idle and loaded, or during the Try-only cooldown after
# a Try broke a worker; it never counts toward the circuit breaker. Its work is sized to its
# deadline: at most TRY_NER_MAX_CHUNKS windows (else 413 text_too_long_for_ner, before any worker
# is asked), one per batch, and the pool checks for waiting proxy requests before every batch, so
# a proxy request that arrives mid-Try waits for ONE window at most, never the whole Try.
# How the budget was chosen (TECNICO «Prueba un texto»): the bench measured GLiNER at p95 3.8 s
# per MEDDOCAN document, several 200-token windows each, on the reference CPU; 1.5 s per window
# is a conservative bound, so 2 windows take about 3 s, under a 5 s deadline. Measure on the
# target machine before raising them.
TRY_THREADS = 1
# 11e-1: worker processes of `antifaz serve` (cli/serve.py OPTIONS; red stub).
SERVE_WORKERS = 1
TRY_NER_SECONDS = 5.0
TRY_NER_MAX_CHUNKS = 2
TRY_NER_BATCH_CHUNKS = 1
# 11d-b: the most NER tokens (about words: chunker.py) TRY_NER_MAX_CHUNKS windows can hold, the
# only source of the «unas N palabras» the panel shows (rendered into the page; a test forbids
# the number in the templates, scripts and catalogs). A longer text is 413 text_too_long_for_ner.
TRY_NER_MAX_WORDS = WINDOW_TOKENS + (TRY_NER_MAX_CHUNKS - 1) * (WINDOW_TOKENS - OVERLAP_TOKENS)


@dataclass
class _Limiters:
    """The capacity limiters, made in the lifespan (on the server's event loop) and read by
    the routes and the panel's Try runner at each call. None before the app starts."""

    mask: anyio.CapacityLimiter | None = None
    try_: anyio.CapacityLimiter | None = None


# FastAPI's native OpenTelemetry (0.142+), off whatever the environment says. Left on, it would
# wrap every request as soon as some library registered a global provider: the path, the query,
# exception messages and stack traces, the request body kept in memory and an incoming
# `traceparent` joined. `auto_configure` False ignores FASTAPI_OTEL_AUTO_CONFIGURE and the OTLP
# variables; `exclude` skips every request in case a future FastAPI turns a signal back on.
TELEMETRY_OFF: TelemetryConfig = {
    "tracing": False,
    "metrics": False,
    "logs": False,
    "operation_spans": False,
    "auto_configure": False,
    "exclude": lambda scope: True,
}


def registered_paths(app: FastAPI) -> frozenset[str]:
    """The paths of every route. `app.routes` holds included routers, not their routes."""
    return frozenset(context.path for context in iter_route_contexts(app.routes) if context.path)


def base_url_parts(url: str) -> "UrlParts | None":
    """A provider base URL exactly as httpx will use it (11c-3b): scheme, the ASCII host httpx
    contacts (IDNA2008), the port and whether there is a user part. None if httpx refuses it.
    Only for the panel's «Estado» facts, which keep nothing but the checked host[:port]."""
    from antifaz.web.status import UrlParts

    try:
        parsed = httpx.URL(url)
        host = parsed.raw_host.decode("ascii")
        return UrlParts(parsed.scheme, host, parsed.port, parsed.userinfo != b"")
    except (httpx.InvalidURL, ValueError, TypeError):  # UnicodeError is a ValueError
        return None


def _panel(
    app: FastAPI,
    settings: Settings,
    ner: NerDetector | None,
    limiters: _Limiters,
    try_ner: NerDetector | None,
) -> Callable[[ASGIApp], ASGIApp] | None:
    """The panel's door and routes (ADR-0018), or None without ANTIFAZ_ADMIN_TOKEN.

    antifaz.web is imported only here, so without the token nothing of the panel is loaded. The
    panel gets a PanelState and nothing else: the token's SHA-256, never the token or settings.
    Its static files are read, checked and hashed here, once (AssetError stops the startup).
    The «Estado» view's facts (11c-3b) are made here too, without a secret: whether each
    provider has a key, the checked host of a custom base URL (never the URL), counts, and a
    callable that reads the NER state at each request (it holds the NER itself, never the
    app). They are kept in app.state.panel_status, so the lifespan marks when start-up finished.

    «Prueba un texto» (11d-a) gets a runner, never the detector, the policy or the settings: it
    runs web/try_text.run_try() with the gateway's policy and detector in a worker thread, behind
    the Try limiter (one, never queued: 503 try_busy) and then the proxy's mask limiter. With the
    NER, each Try gets its own Scanner over `ner.for_try()`: same model and workers, no span
    cache (no membership oracle, nothing evicted or left behind), TRY_NER_MAX_CHUNKS windows at
    most (else 413 text_too_long_for_ner, before any worker is asked), one per batch, under a
    TRY_NER_SECONDS deadline; a worker only if no proxy request waits for one (checked before
    every batch; else 503 ner_busy), no effect on the circuit breaker and a Try-only cooldown
    after a Try breaks a worker. The proxy waits for one Try batch at most.
    A Try with "ner": false (11d-b) runs over the plain scanner (detect.scan.scan): never the NER,
    never the proxy's detector (app.state.detector) and so never its span cache. The answer says
    names_checked: true only when the Try's own NER detector read the text (a NER failure is the
    blocked answer, or 503 ner_busy: never a result without names unless the user asked).
    run_try never calls a provider; the runner holds no client.
    """
    if settings.admin_token is None:
        return None
    from antifaz.http_errors import TextTooLongForNerError, TryBusyError
    from antifaz.web.assets import load_assets
    from antifaz.web.door import PanelDoor, PanelState
    from antifaz.web.forwarded import parse_networks
    from antifaz.web.login_limit import FailureLimiter
    from antifaz.web.routes import build_router
    from antifaz.web.sessions import MemorySessionStore
    from antifaz.web.status import NER_OFF, ProviderFacts, StatusFacts, is_default, provider_address
    from antifaz.web.try_limit import TryLimiter
    from antifaz.web.try_text import run_try

    def provider(id_: Literal["openai", "anthropic"], key: object, url: str) -> ProviderFacts:
        default = Settings.model_fields[f"{id_}_base_url"].default
        parts = base_url_parts(url)
        address, credentials = provider_address(parts, default=is_default(url, default))
        return ProviderFacts(id_, key is not None, address, credentials)

    def ner_state() -> object:
        return NER_OFF if ner is None else ner.status()

    status = StatusFacts(
        version=__version__,
        providers=(
            provider("openai", settings.openai_api_key, settings.openai_base_url),
            provider("anthropic", settings.anthropic_api_key, settings.anthropic_base_url),
        ),
        allowed_hosts=len(settings.allowed_hosts),
        allowed_origins=len(settings.allowed_origins),
        trusted_proxies=len(settings.trusted_proxies),
        ner_state=ner_state,
    )
    detector, policy = app.state.detector, app.state.policy

    async def try_runner(text: str, lang: str, use_ner: bool) -> dict[str, Any]:
        mask_limiter, try_limiter = limiters.mask, limiters.try_
        if mask_limiter is None or try_limiter is None:
            raise RuntimeError("the gateway has not started")
        try:
            try_limiter.acquire_nowait()  # one Try at a time and never a queue
            taken = True
        except anyio.WouldBlock:
            taken = False
        if not taken:
            raise TryBusyError("try_busy")
        isolated = (
            None
            if try_ner is None or not use_ner
            else try_ner.for_try(
                TRY_NER_SECONDS, max_chunks=TRY_NER_MAX_CHUNKS, batch_chunks=TRY_NER_BATCH_CHUNKS
            )
        )
        chosen: Detector = scan_text  # "ner": false: the plain scanner, nothing of the proxy's
        if isolated is not None:
            chosen = Scanner(isolated)
        elif use_ner:
            chosen = detector
        try:
            answer = await anyio.to_thread.run_sync(
                functools.partial(run_try, text, lang, detector=chosen, policy=policy),
                limiter=mask_limiter,
            )
        finally:
            try_limiter.release()
        if isolated is not None and isolated.was_too_long():
            raise TextTooLongForNerError()
        if isolated is not None and isolated.was_busy():
            raise TryBusyError("ner_busy")
        checked = isolated is not None and answer["blocked_reason"] != "detector_failed"
        return {**answer, "names_checked": checked}

    token = settings.admin_token.get_secret_value().encode()
    state = PanelState(
        token_fingerprint=hashlib.sha256(token).digest(),
        trusted_proxies=parse_networks(settings.trusted_proxies),
        sessions=MemorySessionStore(),
        limiter=FailureLimiter(),
        assets=load_assets(),
        status=status,
        try_runner=try_runner,
        try_limiter=TryLimiter(),
        try_ner_words=None if try_ner is None else TRY_NER_MAX_WORDS,
    )
    app.state.panel_status = status
    app.state.panel_state = state
    app.include_router(build_router(state))
    return functools.partial(PanelDoor, state=state)


def create_app(
    settings: Settings | None = None,
    *,
    http_client: httpx.AsyncClient | None = None,
    detector: Detector | None = None,
    policy: Policy = DEFAULT_POLICY,
    ner: NerDetector | None = None,
) -> FastAPI:
    """Build the app. Tests inject `http_client` (httpx.MockTransport), `detector` and `ner`
    (a NER detector on the fake backend).

    Raises UnsafeConfigError (without any key in the message) if the gateway would start open,
    or if the NER is on and its model or backend is missing (ADR-0016). The NER workers start
    with the app (a model that cannot load stops the startup) and stop with it.
    """
    settings = settings or get_settings()
    check_safe_to_start(settings)
    ner = ner if ner is not None else ner_from_settings(settings)
    configure_logging(settings.log_level)
    api_key = settings.antifaz_api_key.get_secret_value() if settings.antifaz_api_key else ""
    limiters = _Limiters()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = http_client or build_client(settings)
        app.state.http_client = client
        limiters.mask = app.state.mask_limiter = anyio.CapacityLimiter(MASK_THREADS)
        limiters.try_ = app.state.try_limiter = anyio.CapacityLimiter(TRY_THREADS)
        # Starting and stopping the workers use a limiter of their own: a busy gateway never
        # delays its own shutdown.
        lifecycle = anyio.CapacityLimiter(1)
        try:
            if ner is not None:
                await anyio.to_thread.run_sync(ner.start, limiter=lifecycle)
            panel_status = getattr(app.state, "panel_status", None)
            if panel_status is not None:
                panel_status.mark_started()  # the Status view's uptime counts from here
            yield
        finally:
            if ner is not None:
                await anyio.to_thread.run_sync(ner.close, limiter=lifecycle)
            if http_client is None:  # an injected client belongs to whoever created it
                await client.aclose()

    # No /docs, /redoc or /openapi.json (fewer public surfaces) and no slash redirects: an
    # alias such as "/v1/messages/" is a 404, never a 307 to a URL built from the Host header.
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
        telemetry=TELEMETRY_OFF,
    )
    app.state.settings = settings
    app.state.detector = detector or (Scanner(ner) if ner is not None else scan_text)
    app.state.ner = ner
    app.state.policy = policy
    app.state.stream_counters = StreamCounters()
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(openai.router)
    app.include_router(anthropic.router)
    app.include_router(models.router)
    app.include_router(scan.router)
    # A Try gets its own NER detector only when the gateway built the detector over the NER.
    try_ner = ner if detector is None else None
    panel_door = _panel(app, settings, ner, limiters, try_ner)
    app.state.route_paths = registered_paths(app)
    # The last one added runs first: request id -> trusted host -> gate (the panel's door for
    # /panel*; the key, Origin and JSON for the rest).
    app.add_middleware(
        GateMiddleware,
        api_key=api_key,
        allowed_origins=settings.allowed_origins,
        panel_door=panel_door,
    )
    app.add_middleware(
        CaseInsensitiveTrustedHost, allowed_hosts=settings.allowed_hosts, www_redirect=False
    )
    app.middleware("http")(request_id_middleware)
    return app
