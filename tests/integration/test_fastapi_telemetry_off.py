"""FastAPI's native OpenTelemetry stays off (Dependabot PR #79, fastapi 0.143).

Since 0.142 FastAPI imports `opentelemetry-api` and wraps every request in spans, metrics and
logs as soon as a global non-proxy provider exists (another library or a sitecustomize could
register one). It would record the path, the query, exception messages and stack traces, keep
the request body and parsed values in its TelemetryData, and honour an incoming `traceparent`.
Antifaz passes `telemetry={...}` with everything off; these tests prove it with fake global
providers that record every call. No opentelemetry-sdk, no exporter, no network.
"""

import logging
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import _logs, metrics, trace
from opentelemetry._logs import Logger, LoggerProvider, NoOpLogger
from opentelemetry.metrics import Meter, MeterProvider, NoOpMeter
from opentelemetry.trace import NonRecordingSpan, NoOpTracer, Span, Tracer, TracerProvider

from antifaz.api.app import create_app
from tests.conftest import forbid_network
from tests.integration.fakes import FakeUpstream
from tests.integration.panel_client import HTTPS_URL, clients, post_login
from tests.redteam.conftest import (
    ANTHROPIC_AUTH,
    DNI,
    OPENAI_AUTH,
    anthropic_body,
    both_echo,
    gateway_client,
    gateway_settings,
    openai_body,
)

# A valid W3C trace context that a client could send to make the gateway join its trace.
TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
TRACEPARENT = f"00-{TRACE_ID}-00f067aa0ba902b7-01"


class Calls:
    """Every call that reached a fake provider, its tracers, meters or loggers."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def add(self, call: str) -> None:
        self.calls.append(call)


class RecordingTracer(NoOpTracer):
    """Honours the parent context like a real tracer: a span on the incoming trace."""

    def __init__(self, calls: Calls) -> None:
        self._calls = calls

    def start_span(self, name: str, context: Any = None, *args: Any, **kwargs: Any) -> Span:
        self._calls.add(f"span {name}")
        return NonRecordingSpan(trace.get_current_span(context).get_span_context())


class RecordingTracerProvider(TracerProvider):
    def __init__(self, calls: Calls) -> None:
        self._calls = calls

    def get_tracer(self, *args: Any, **kwargs: Any) -> Tracer:
        self._calls.add("get_tracer")
        return RecordingTracer(self._calls)


class RecordingMeter(NoOpMeter):
    def __init__(self, calls: Calls) -> None:
        super().__init__("fake")
        self._calls = calls

    def create_histogram(self, name: str, *args: Any, **kwargs: Any) -> Any:
        self._calls.add(f"histogram {name}")
        return super().create_histogram(name, *args, **kwargs)

    def create_up_down_counter(self, name: str, *args: Any, **kwargs: Any) -> Any:
        self._calls.add(f"counter {name}")
        return super().create_up_down_counter(name, *args, **kwargs)


class RecordingMeterProvider(MeterProvider):
    def __init__(self, calls: Calls) -> None:
        self._calls = calls

    def get_meter(self, *args: Any, **kwargs: Any) -> Meter:
        self._calls.add("get_meter")
        return RecordingMeter(self._calls)


class RecordingLogger(NoOpLogger):
    def __init__(self, calls: Calls) -> None:
        super().__init__("fake")
        self._calls = calls

    def emit(self, *args: Any, **kwargs: Any) -> None:
        self._calls.add("emit")


class RecordingLoggerProvider(LoggerProvider):
    def __init__(self, calls: Calls) -> None:
        self._calls = calls

    def get_logger(self, *args: Any, **kwargs: Any) -> Logger:
        self._calls.add("get_logger")
        return RecordingLogger(self._calls)


@pytest.fixture
def otel(monkeypatch: pytest.MonkeyPatch) -> Iterator[Calls]:
    """Fake global, non-proxy tracer, meter and logger providers, as if an SDK were set up.

    They go into the API's own globals (what set_*_provider writes, which only works once per
    process), so monkeypatch puts the proxies back after the test. Setting the globals directly
    skips the proxies' on_set_* rebinding, so nothing created meanwhile latches onto the fakes.
    """
    calls = Calls()
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", RecordingTracerProvider(calls))
    monkeypatch.setattr(metrics._internal, "_METER_PROVIDER", RecordingMeterProvider(calls))
    monkeypatch.setattr(_logs._internal, "_LOGGER_PROVIDER", RecordingLoggerProvider(calls))
    assert isinstance(trace.get_tracer_provider(), RecordingTracerProvider)
    assert isinstance(metrics.get_meter_provider(), RecordingMeterProvider)
    assert isinstance(_logs.get_logger_provider(), RecordingLoggerProvider)
    yield calls


def _native_telemetry_enabled(app: FastAPI) -> bool:
    # FastAPI keeps no public flag: its own check, the one that decides whether to wrap a request.
    return bool(app._native_telemetry.enabled())


def _gateway_app(upstream: FakeUpstream) -> FastAPI:
    """The gateway app without entering it, so a test can change it before the lifespan runs."""
    http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    return create_app(gateway_settings(), http_client=http)


def _auto_configure_env(monkeypatch: pytest.MonkeyPatch) -> None:
    forbid_network(monkeypatch)
    monkeypatch.setenv("FASTAPI_OTEL_AUTO_CONFIGURE", "true")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector.invalid:4318")
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "otlp")
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "otlp")
    monkeypatch.setenv("OTEL_LOGS_EXPORTER", "otlp")


AUTO_CONFIGURE_FAILED = "automatic telemetry configuration failed"


def test_the_fakes_would_be_used_by_a_default_fastapi_app(otel: Calls) -> None:
    """Control: the same providers turn telemetry on for an app built without `telemetry`."""
    app = FastAPI()

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret detail")

    with TestClient(app, raise_server_exceptions=False) as client:
        client.get("/boom?dni=1")
    assert _native_telemetry_enabled(app)
    assert {"get_tracer", "get_meter", "get_logger", "emit"} <= set(otel.calls)


def test_a_proxy_request_produces_no_span_metric_or_log(otel: Calls) -> None:
    upstream = FakeUpstream(both_echo)
    for client in gateway_client(upstream):
        app = client.app
        assert isinstance(app, FastAPI)
        assert not _native_telemetry_enabled(app)
        client.get("/healthz")
        client.post("/v1/chat/completions", json=openai_body(f"DNI {DNI}"), headers=OPENAI_AUTH)
        client.post("/v1/messages", json=anthropic_body("hola"), headers=ANTHROPIC_AUTH)
        broken = client.post(
            "/v1/chat/completions",
            content=b"{not json",
            headers={**OPENAI_AUTH, "Content-Type": "application/json"},
        )
        assert 400 <= broken.status_code < 500
    assert len(upstream.requests) == 2
    assert otel.calls == []


def test_a_panel_request_produces_no_span_metric_or_log(otel: Calls) -> None:
    for client in clients(HTTPS_URL):
        app = client.app
        assert isinstance(app, FastAPI)
        assert not _native_telemetry_enabled(app)
        assert client.get("/panel").status_code == 303
        assert post_login(client).status_code == 200
    assert otel.calls == []


def test_fastapi_exporter_auto_configuration_stays_off(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """FASTAPI_OTEL_AUTO_CONFIGURE=true with OTLP variables: nothing is set up, no socket.

    Without the SDK FastAPI would log "automatic telemetry configuration failed" and go on;
    with it, it would register exporters to the endpoint. Antifaz never gets that far.
    """
    _auto_configure_env(monkeypatch)
    proxy_tracer_provider = trace.get_tracer_provider()

    with caplog.at_level(logging.DEBUG):
        for client in gateway_client(FakeUpstream(both_echo)):
            assert client.get("/healthz").status_code == 200
    assert "telemetry" not in caplog.text.lower()
    assert trace.get_tracer_provider() is proxy_tracer_provider


def test_the_auto_configuration_control_logs_in_a_default_app(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Control: the same variables do reach FastAPI's auto-configuration in a default app (no
    SDK installed, so it logs the failure and starts), so the test above can see it."""
    _auto_configure_env(monkeypatch)
    with caplog.at_level(logging.DEBUG), TestClient(FastAPI()) as client:
        assert client.get("/missing").status_code == 404
    assert AUTO_CONFIGURE_FAILED in caplog.text.lower()


def test_auto_configure_false_alone_holds_with_the_signals_on(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """With tracing, metrics and logs forced on (so FastAPI would ask for exporters), the
    explicit auto_configure=False still sets nothing up."""
    _auto_configure_env(monkeypatch)
    app = _gateway_app(FakeUpstream(both_echo))
    app._telemetry.update({"tracing": True, "metrics": True, "logs": True})
    with caplog.at_level(logging.DEBUG), TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
    assert "telemetry" not in caplog.text.lower()


def test_an_incoming_traceparent_is_neither_joined_nor_sent_upstream(otel: Calls) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        # The fake provider runs inside the request's context: the span FastAPI would have made.
        context = trace.get_current_span().get_span_context()
        seen.append(format(context.trace_id, "032x") if context.is_valid else "none")
        return both_echo(request)

    upstream = FakeUpstream(handler)
    headers = {**OPENAI_AUTH, "traceparent": TRACEPARENT, "tracestate": "vendor=1"}
    for client in gateway_client(upstream, gateway_settings()):
        response = client.post("/v1/chat/completions", json=openai_body("hola"), headers=headers)
        assert response.status_code == 200
    assert seen == ["none"]
    assert otel.calls == []
    sent = upstream.requests[0].headers
    assert "traceparent" not in sent and "tracestate" not in sent
    assert TRACE_ID not in str(dict(sent))


def test_the_traceparent_control_is_joined_when_tracing_is_forced_on(otel: Calls) -> None:
    """Control: with tracing on and no exclusion, the upstream handler does run inside the
    client's trace (so the test above can see it), and the gateway still does not forward
    `traceparent` to the provider."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        context = trace.get_current_span().get_span_context()
        seen.append(format(context.trace_id, "032x") if context.is_valid else "none")
        return both_echo(request)

    upstream = FakeUpstream(handler)
    app = _gateway_app(upstream)
    app._telemetry.update({"tracing": True, "exclude": None})
    headers = {**OPENAI_AUTH, "traceparent": TRACEPARENT}
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", json=openai_body("hola"), headers=headers)
        assert response.status_code == 200
    assert seen == [TRACE_ID]
    assert "get_tracer" in otel.calls
    assert "traceparent" not in upstream.requests[0].headers


def test_the_exclusion_still_skips_requests_if_a_signal_came_back_on(otel: Calls) -> None:
    """Fail-closed: should a future FastAPI turn a signal on despite the settings, `exclude`
    (which FastAPI checks before anything else) still skips every request."""
    upstream = FakeUpstream(both_echo)
    for client in gateway_client(upstream):
        app = client.app
        assert isinstance(app, FastAPI)
        app._telemetry.update({"tracing": True, "metrics": True, "logs": True})
        assert _native_telemetry_enabled(app)
        response = client.post(
            "/v1/chat/completions",
            json=openai_body("hola"),
            headers={**OPENAI_AUTH, "traceparent": TRACEPARENT},
        )
        assert response.status_code == 200
    assert otel.calls == []


def test_create_app_builds_a_single_fastapi_app() -> None:
    """The settings only cover the app built here: no other FastAPI app or mount exists."""
    app = create_app(gateway_settings())
    assert not any(type(route).__name__ == "Mount" for route in app.routes)
    assert not _native_telemetry_enabled(app)
