"""Red team of streaming (part 5c): cuts, slow and failing providers, keys and foreign markers.

Every attack runs against both providers with the fake upstream, which records the bytes it
receives. Only synthetic data and fake keys.
"""

import asyncio
import json
import logging
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from antifaz.api.app import create_app
from antifaz.providers.sse import MAX_LINE_CHARS
from antifaz.restore import MAX_HOLDBACK
from tests.integration import test_proxy_anthropic as anth
from tests.integration import test_proxy_openai as oai
from tests.integration.fakes import (
    ChunkStream,
    FakeUpstream,
    anthropic_event,
    anthropic_sse,
    anthropic_text,
    misses_repeats,
    openai_sse,
    openai_text,
    sse_payloads,
    sse_response,
)
from tests.redteam.conftest import (
    ANTHROPIC_AUTH,
    DNI,
    IBAN,
    OPENAI_AUTH,
    anthropic_body,
    assert_not_sent,
    gateway_settings,
    openai_body,
)

BS = chr(92)  # a backslash, for JSON escapes written by hand

Build = Callable[..., str]
ROUTES: dict[str, tuple[Any, str, dict[str, str], Callable[..., dict[str, Any]], Build]] = {
    "openai": (oai, "/v1/chat/completions", OPENAI_AUTH, openai_body, openai_sse),
    "anthropic": (anth, "/v1/messages", ANTHROPIC_AUTH, anthropic_body, anthropic_sse),
}
TEXT_OF: dict[str, Callable[[str], str]] = {"openai": openai_text, "anthropic": anthropic_text}


def _masked_text(request: httpx.Request) -> str:
    body = json.loads(request.content)
    content = body["messages"][-1]["content"]
    return content if isinstance(content, str) else content[0]["text"]


def _post(route: str, client: TestClient, text: str) -> httpx.Response:
    _, path, auth, body, _ = ROUTES[route]
    return client.post(path, json=body(text, stream=True), headers=auth)


def _clients(route: str, handler: Callable[[httpx.Request], httpx.Response], **kw: Any) -> Any:
    module = ROUTES[route][0]
    upstream = FakeUpstream(handler)
    return upstream, module._client(upstream, **kw)


def _up_to_the_event_with(text: str, needle: str) -> str:
    """`text` cut right after the event that holds `needle`: the rest never arrives."""
    return text[: text.index("\n\n", text.index(needle)) + 2]


def _error_event(route: str, text: str) -> dict[str, Any]:
    last = sse_payloads(text)[-1]
    assert isinstance(last, dict)
    if route == "openai":
        assert last["error"]["type"] == "antifaz_error"
        return {"code": last["error"]["code"], "message": last["error"]["message"]}
    assert last["type"] == "error"
    return {"message": last["error"]["message"]}


# --- Provider errors on a stream request ------------------------------------------------------


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("status", [429, 500, 503, 529])
def test_error_del_proveedor_en_stream_se_devuelve_sin_datos(
    route: str, status: int, caplog: pytest.LogCaptureFixture
) -> None:
    """El proveedor rechaza el stream (429, 5xx) copiando lo que recibió: solo lleva marcadores."""
    caplog.set_level(logging.DEBUG)

    def fail(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"echo": request.content.decode()}})

    upstream, clients = _clients(route, fail)
    for client in clients:
        response = _post(route, client, f"DNI {DNI} IBAN {IBAN}")
        assert response.status_code == status
        assert "[[ES_DNI_1]]" in response.text
        assert DNI not in response.text
        assert IBAN not in response.text
    assert DNI not in caplog.text
    assert_not_sent(upstream, DNI, IBAN)


@pytest.mark.parametrize("route", ROUTES)
def test_error_en_sse_del_proveedor_pasa_tal_cual(route: str) -> None:
    """El proveedor manda su error como evento SSE a mitad del stream: pasa, no se inventa nada."""
    if route == "openai":
        error = 'data: {"error": {"message": "overloaded [[ES_DNI_1]]"}}\n\n'
    else:
        error = anthropic_event({"type": "error", "error": {"message": "overloaded [[ES_DNI_1]]"}})
    _, clients = _clients(route, lambda _: sse_response(error)[0])
    for client in clients:
        response = _post(route, client, f"DNI {DNI}")
        assert response.status_code == 200
        assert response.text.startswith(error)  # untouched: no restore in provider errors
        assert DNI not in response.text


# --- Slow, cut and failing streams ------------------------------------------------------------


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(("split", "delay"), [(1, 0.0), (64, 0.02)], ids=["1-byte", "slow"])
def test_trozos_de_un_byte_y_proveedor_lento(route: str, split: int, delay: float) -> None:
    """Trozos de 1 byte que cortan caracteres UTF-8 y marcadores; o trozos que llegan despacio."""
    original = f"Ñandú 😀 € DNI {DNI} y [[ESC]] con [[!"
    build = ROUTES[route][4]

    def slow(request: httpx.Request) -> httpx.Response:
        masked = _masked_text(request)
        return sse_response(build([masked[:9], masked[9:]]), split=split, delay=delay)[0]

    upstream, clients = _clients(route, slow)
    for client in clients:
        response = _post(route, client, original)
        assert response.status_code == 200
        assert TEXT_OF[route](response.text) == original
    assert_not_sent(upstream, DNI)


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    ("error", "code"),
    [
        (httpx.RemoteProtocolError, "upstream_unavailable"),
        (httpx.ReadTimeout, "upstream_timeout"),
        (httpx.ReadError, "upstream_unavailable"),
    ],
)
def test_stream_cortado_en_mitad_de_un_marcador(
    route: str, error: type[httpx.HTTPError], code: str, caplog: pytest.LogCaptureFixture
) -> None:
    """La conexión se corta con "[[ES_DN" a medias y un error que lleva un DNI en su mensaje."""
    caplog.set_level(logging.DEBUG)
    build = ROUTES[route][4]

    def cut(request: httpx.Request) -> httpx.Response:
        whole = build(["Tu DNI: [[ES_DNI_1]] y el otro [[ES_DN", "I_1]] fin"])
        failure = error(f"boom {DNI}", request=request)
        return sse_response(_up_to_the_event_with(whole, '[[ES_DN"'), error=failure)[0]

    _, clients = _clients(route, cut)
    for client in clients:
        response = _post(route, client, f"DNI {DNI}")
        assert response.status_code == 200  # the headers were already sent
        # The safe part restored; the half placeholder shown as it is (it holds no data).
        assert TEXT_OF[route](response.text) == f"Tu DNI: {DNI} y el otro [[ES_DN"
        event = _error_event(route, response.text)
        if route == "openai":
            assert event["code"] == code
        assert DNI not in json.dumps(event)
    assert DNI not in caplog.text


@pytest.mark.parametrize("route", ROUTES)
def test_placeholder_incompleto_al_cortar_sale_tal_cual(route: str) -> None:
    """El stream acaba sin su último evento con un marcador a medias: sale como marcador."""
    build = ROUTES[route][4]

    def unfinished(request: httpx.Request) -> httpx.Response:
        whole = build(["ok [[ES_DNI_1]] [[ES_DNI", "_1]]"])
        return sse_response(_up_to_the_event_with(whole, '[[ES_DNI"'))[0]

    _, clients = _clients(route, unfinished)
    for client in clients:
        response = _post(route, client, f"DNI {DNI}")
        assert TEXT_OF[route](response.text) == f"ok {DNI} [[ES_DNI"
        event = _error_event(route, response.text)
        assert event["message"] == "the provider's stream ended before it was complete"


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    "body",
    [
        b"data: \xff\xfe\n\n",  # not UTF-8
        b"data: " + b"a" * (MAX_LINE_CHARS + 10),  # one endless line
        "data: ñ".encode()[:-1],  # a character cut at the very end
    ],
    ids=["invalid-utf8", "endless-line", "cut-character"],
)
def test_stream_malformado_termina_con_error_fijo(route: str, body: bytes) -> None:
    """Bytes que no son SSE válido: error fijo, sin repetir lo recibido."""
    _, clients = _clients(route, lambda _: sse_response(body, split=4096)[0])
    for client in clients:
        response = _post(route, client, "hola")
        event = _error_event(route, response.text)
        assert "aaaa" not in response.text
        assert event["message"] in (
            "the provider sent a malformed stream",
            "the provider's stream ended before it was complete",
        )


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    "text",
    [
        "hola [[" + " " * (MAX_HOLDBACK * 3) + "fin]]",
        "enlace [[" + "Pagina_De_Ejemplo_Muy_Larga_" * 4 + "]] y sigue",
        "tabla [[" + "A" * 200,
    ],
    ids=["spaces", "wiki-link", "letters"],
)
def test_corchetes_largos_legitimos_no_cortan_el_stream(route: str, text: str) -> None:
    """Un texto normal con "[[" y mucho detrás (enlace wiki, espacios) llega entero y sin error."""
    build = ROUTES[route][4]
    _, clients = _clients(route, lambda _: sse_response(build([text[:40], text[40:]]))[0])
    for client in clients:
        response = _post(route, client, f"DNI {DNI}")
        assert TEXT_OF[route](response.text) == text
        assert "antifaz_error" not in response.text
        assert "event: error" not in response.text


# --- Markers, reasoning and unknown events ------------------------------------------------------


@pytest.mark.parametrize("route", ROUTES)
def test_marcadores_de_otra_peticion_no_se_restauran(route: str) -> None:
    """Una petición con DNI; la siguiente, sin DNI, recibe [[ES_DNI_1]]: no se convierte en dato."""
    build = ROUTES[route][4]
    upstream, clients = _clients(
        route, lambda _: sse_response(build(["Te digo [[ES_DNI_1]] y [[ES_DNI_", "2]]"]))[0]
    )
    for client in clients:
        first = _post(route, client, f"DNI {DNI}")
        assert TEXT_OF[route](first.text) == f"Te digo {DNI} y [[ES_DNI_2]]"
        second = _post(route, client, "sin datos")
        assert TEXT_OF[route](second.text) == "Te digo [[ES_DNI_1]] y [[ES_DNI_2]]"
        assert DNI not in second.text
    assert len(upstream.requests) == 2


def test_thinking_en_stream_no_se_toca_aunque_lleve_marcadores_de_esta_peticion() -> None:
    """El razonamiento trae [[ES_DNI_1]] (que sí es de esta petición): sale sin restaurar."""
    thinking = anthropic_event(
        {
            "type": "content_block_delta",
            "index": 5,
            "delta": {"type": "thinking_delta", "thinking": "El [[ES_DNI_1]] es [[!"},
        }
    )
    signature = anthropic_event(
        {
            "type": "content_block_delta",
            "index": 5,
            "delta": {"type": "signature_delta", "signature": "c2lnbmF0dXJh[[ES_DNI_1]]=="},
        }
    )
    start = anthropic_event(
        {"type": "content_block_start", "index": 5, "content_block": {"type": "thinking"}}
    )
    stop = anthropic_event({"type": "content_block_stop", "index": 5})
    body = start + thinking + signature + stop + anthropic_sse(["fin"])
    _, clients = _clients("anthropic", lambda _: sse_response(body, split=3)[0])
    for client in clients:
        response = _post("anthropic", client, f"DNI {DNI}")
        assert thinking in response.text
        assert signature in response.text
        assert DNI not in response.text


@pytest.mark.parametrize("route", ROUTES)
def test_eventos_desconocidos_pasan_sin_tocar_y_se_cuentan(route: str) -> None:
    """Un evento nuevo con un marcador dentro: no se bloquea, no se restaura, se cuenta."""
    build = ROUTES[route][4]
    unknown = 'event: futuro\ndata: {"type": "futuro", "texto": "[[ES_DNI_1]]"}\n\n'
    upstream = FakeUpstream(lambda _: sse_response(unknown + build(["hola"]))[0])
    module = ROUTES[route][0]
    http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    app = create_app(module._settings(), http_client=http)
    with TestClient(app) as client:
        response = _post(route, client, f"DNI {DNI}")
        assert response.text.startswith(unknown)
        assert DNI not in response.text
        assert app.state.stream_counters.unknown_events == 1
        assert app.state.stream_counters.failed_streams == 0


# --- The guard and the keys (invariant 13) ----------------------------------------------------


@pytest.mark.parametrize("route", ROUTES)
def test_la_guardia_revisa_la_peticion_en_stream(route: str) -> None:
    """El detector se salta la repetición: la guardia bloquea antes de abrir el stream."""
    upstream, clients = _clients(route, lambda _: sse_response("")[0], detector=misses_repeats)
    for client in clients:
        response = _post(route, client, f"{DNI} y otra vez {DNI}")
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "antifaz_blocked"
    assert upstream.requests == []


CANARY = "canary-provider-key-not-real-5d7e9f1a3c"
GATEWAY_CANARY = "canary-gateway-key-not-real-2b4d6f8a0c1e"


def _canary_client(upstream: FakeUpstream) -> Iterator[TestClient]:
    settings = gateway_settings(
        antifaz_api_key=SecretStr(GATEWAY_CANARY),
        openai_api_key=SecretStr(CANARY),
        anthropic_api_key=SecretStr(CANARY),
        log_level="DEBUG",
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    with TestClient(create_app(settings, http_client=http)) as client:
        yield client


def _escaped(key: str) -> str:
    return "".join(f"{BS}u{ord(c):04x}" for c in key)


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("how", ["raw", "escaped", "split", "comment", "content-type"])
def test_la_clave_nunca_sale_en_el_stream(
    route: str, how: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Un proveedor devuelve su clave en el stream: tal cual, escapada, partida o en la cabecera."""
    caplog.set_level(logging.DEBUG)
    build = ROUTES[route][4]
    pieces = {
        "raw": ["ok ", f"clave {CANARY}"],
        "split": ["ok ", CANARY[:12], CANARY[12:]],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if how == "content-type":
            return sse_response(build(["hola"]), content_type=f"text/event-stream; k={CANARY}")[0]
        if how == "comment":
            return sse_response(f": {CANARY}\n\n" + build(["hola"]))[0]
        if how == "escaped":
            text = build(["ok ", "PLACEHOLDER"]).replace("PLACEHOLDER", _escaped(CANARY))
            return sse_response(text)[0]
        return sse_response(build(pieces[how]))[0]

    auth = {"x-api-key": GATEWAY_CANARY, "anthropic-version": "2023-06-01"}
    upstream = FakeUpstream(handler)
    for client in _canary_client(upstream):
        path, body = ROUTES[route][1], ROUTES[route][3]
        response = client.post(path, json=body("hola", stream=True), headers=auth)
        unescaped = response.text.encode("ascii", "replace").decode("unicode_escape", "replace")
        for seen in (response.text, unescaped, str(response.headers.raw), caplog.text):
            assert CANARY not in seen, how
            assert GATEWAY_CANARY not in seen, how
        if how == "content-type":
            assert response.status_code == 502
        else:
            assert "contained a key" in response.text


def _assert_key_dropped(response: httpx.Response, caplog: pytest.LogCaptureFixture) -> None:
    unescaped = response.text.encode("ascii", "replace").decode("unicode_escape", "replace")
    twice = unescaped.encode("ascii", "replace").decode("unicode_escape", "replace")
    for seen in (response.text, unescaped, twice, str(response.headers.raw), caplog.text):
        assert CANARY not in seen
        assert GATEWAY_CANARY not in seen
    assert "contained a key" in response.text


CANARY_AUTH = {"x-api-key": GATEWAY_CANARY, "anthropic-version": "2023-06-01"}


def _openai_tool_stream(pieces: list[str]) -> str:
    first = {"index": 0, "id": "c1", "type": "function", "function": {"name": "f"}}
    calls = [first] + [{"index": 0, "function": {"arguments": p}} for p in pieces]
    chunks = [{"choices": [{"index": 0, "delta": {"tool_calls": [c]}}]} for c in calls]
    chunks.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def _anthropic_tool_stream(pieces: list[str]) -> str:
    tool = {"type": "tool_use", "id": "t", "name": "f", "input": {}}
    events = [anthropic_event({"type": "content_block_start", "index": 0, "content_block": tool})]
    events += [
        anthropic_event(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": p},
            }
        )
        for p in pieces
    ]
    events.append(anthropic_event({"type": "content_block_stop", "index": 0}))
    return "".join(events) + anthropic_event({"type": "message_stop"})


TOOL_STREAM = {"openai": _openai_tool_stream, "anthropic": _anthropic_tool_stream}


@pytest.mark.parametrize("route", ROUTES)
def test_clave_escapada_dos_veces_en_argumentos_del_stream(
    route: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Los argumentos traen la clave escapada en su JSON: al restaurar saldría en claro."""
    caplog.set_level(logging.DEBUG)
    arguments = '{"a": "' + _escaped(CANARY) + '"}'
    pieces = [arguments[i : i + 9] for i in range(0, len(arguments), 9)]
    upstream = FakeUpstream(lambda _: sse_response(TOOL_STREAM[route](pieces))[0])
    for client in _canary_client(upstream):
        path, body = ROUTES[route][1], ROUTES[route][3]
        response = client.post(path, json=body("hola", stream=True), headers=CANARY_AUTH)
        _assert_key_dropped(response, caplog)


@pytest.mark.parametrize("route", ROUTES)
def test_clave_escapada_en_herramientas_sin_stream(
    route: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Sin stream: la clave escapada dentro de arguments o de tool_use.input da un 502 fijo."""
    caplog.set_level(logging.DEBUG)
    if route == "openai":
        call = {
            "id": "c1",
            "type": "function",
            "function": {"name": "f", "arguments": '{"a": "' + _escaped(CANARY) + '"}'},
        }
        answer = {"choices": [{"index": 0, "message": {"role": "assistant", "tool_calls": [call]}}]}
    else:
        tool = {"type": "tool_use", "id": "t", "name": "f", "input": {"a": _escaped(CANARY)}}
        answer = {"type": "message", "role": "assistant", "content": [tool]}
    upstream = FakeUpstream(lambda _: httpx.Response(200, json=answer))
    for client in _canary_client(upstream):
        path, body = ROUTES[route][1], ROUTES[route][3]
        response = client.post(path, json=body("hola"), headers=CANARY_AUTH)
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "bad_upstream_response"
        _assert_key_dropped(response, caplog)


def _openai_chunks(*deltas: tuple[int, dict[str, Any]]) -> str:
    chunks = [{"choices": [{"index": i, "delta": d}]} for i, d in deltas]
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def _anthropic_deltas(*deltas: tuple[int, dict[str, Any]]) -> str:
    events = [
        anthropic_event({"type": "content_block_delta", "index": i, "delta": d}) for i, d in deltas
    ]
    return "".join(events) + anthropic_event({"type": "message_stop"})


HEAD, TAIL = CANARY[:12], CANARY[12:]
INTERLEAVED = {
    "choices": (
        "openai",
        _openai_chunks((0, {"content": HEAD}), (1, {"content": "otra"}), (0, {"content": TAIL})),
    ),
    "content-refusal": (
        "openai",
        _openai_chunks((0, {"content": HEAD}), (0, {"refusal": "no"}), (0, {"content": TAIL})),
    ),
    "reasoning_content": (
        "openai",
        _openai_chunks(
            (0, {"reasoning_content": HEAD}),
            (0, {"content": "x"}),
            (0, {"reasoning_content": TAIL}),
        ),
    ),
    "text-thinking": (
        "anthropic",
        _anthropic_deltas(
            (0, {"type": "text_delta", "text": HEAD}),
            (1, {"type": "thinking_delta", "thinking": "pienso"}),
            (0, {"type": "text_delta", "text": TAIL}),
        ),
    ),
    "citations": (
        "anthropic",
        _anthropic_deltas(
            (2, {"type": "citations_delta", "citation": {"cited_text": HEAD}}),
            (0, {"type": "text_delta", "text": "x"}),
            (2, {"type": "citations_delta", "citation": {"cited_text": TAIL}}),
        ),
    ),
}


@pytest.mark.parametrize("case", INTERLEAVED)
def test_clave_partida_entre_campos_intercalados(
    case: str, caplog: pytest.LogCaptureFixture
) -> None:
    """La clave llega partida en un campo, con otros campos o choices en medio."""
    caplog.set_level(logging.DEBUG)
    route, stream = INTERLEAVED[case]
    upstream = FakeUpstream(lambda _: sse_response(stream)[0])
    for client in _canary_client(upstream):
        path, body = ROUTES[route][1], ROUTES[route][3]
        response = client.post(path, json=body("hola", stream=True), headers=CANARY_AUTH)
        assert "contained a key" in response.text, case
        assert CANARY not in response.text.replace("\n", "").replace('"', ""), case


# --- The client goes away -------------------------------------------------------------------------


@pytest.mark.parametrize("route", ROUTES)
async def test_si_el_cliente_se_va_se_cierra_el_stream_del_proveedor(route: str) -> None:
    """El cliente se va a mitad: la pasarela no deja abierto el stream del proveedor."""
    module, path, auth, body_of, build = ROUTES[route]
    streams: list[ChunkStream] = []

    def hanging(request: httpx.Request) -> httpx.Response:
        response, stream = sse_response(build(["hola ", "mundo"])[:-60], hang=True)
        streams.append(stream)
        return response

    http = httpx.AsyncClient(transport=httpx.MockTransport(FakeUpstream(hanging)))
    app = create_app(module._settings(), http_client=http)
    body = json.dumps(body_of("hola", stream=True)).encode()
    headers = [(name.lower().encode(), value.encode()) for name, value in auth.items()]
    headers += [(b"host", b"testserver"), (b"content-type", b"application/json")]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
        "state": {},
    }
    first_body = asyncio.Event()
    calls = 0

    async def receive() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"type": "http.request", "body": body, "more_body": False}
        await first_body.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.body" and message.get("body"):
            first_body.set()

    async with app.router.lifespan_context(app):
        await asyncio.wait_for(app(scope, receive, send), timeout=5)
    for _ in range(10):
        await asyncio.sleep(0)
    assert first_body.is_set()
    assert len(streams) == 1
    assert streams[0].closed
