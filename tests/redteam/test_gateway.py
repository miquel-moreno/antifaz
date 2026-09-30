"""Red team de la puerta (issue 20): JSON ambiguo, navegador, Host, arranque y trucos de ruta.

Cada test es un ataque: el atacante intenta que algo llegue al proveedor sin revisar, usar la
pasarela desde una web ajena o arrancarla abierta. Solo datos inventados y claves falsas.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.websockets import WebSocketDisconnect

from antifaz.api.app import create_app
from antifaz.config import Settings, UnsafeConfigError
from tests.integration.fakes import GATEWAY_KEY, FakeUpstream
from tests.redteam.conftest import (
    DNI,
    GATEWAY_BODY,
    assert_not_sent,
    gateway_client,
    gateway_settings,
)

PROXY_PATHS = ("/v1/chat/completions", "/v1/messages", "/v1/messages/count_tokens")
AUTH = {"Authorization": f"Bearer {GATEWAY_KEY}"}
JSON = {**AUTH, "Content-Type": "application/json"}
REPO = Path(__file__).resolve().parents[2]


def _raw(proxy: TestClient, path: str, raw: str, headers: dict[str, str] = JSON) -> int:
    return proxy.post(path, content=raw.encode(), headers=headers).status_code


# --- JSON ambiguo: claves duplicadas ---------------------------------------------------------


@pytest.mark.parametrize("path", PROXY_PATHS)
def test_clave_duplicada_arriba_se_rechaza(
    gateway: TestClient, upstream_any: FakeUpstream, path: str
) -> None:
    """El atacante manda `messages` dos veces: una con el DNI y otra limpia."""
    raw = (
        '{"model": "m", "max_tokens": 8,'
        f' "messages": [{{"role": "user", "content": "DNI {DNI}"}}],'
        ' "messages": [{"role": "user", "content": "hola"}]}'
    )
    response = gateway.post(path, content=raw.encode(), headers=JSON)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert DNI not in response.text
    assert upstream_any.requests == []


@pytest.mark.parametrize("path", PROXY_PATHS)
def test_clave_duplicada_anidada_se_rechaza(
    gateway: TestClient, upstream_any: FakeUpstream, path: str
) -> None:
    """El atacante repite `content` dentro de un mensaje."""
    raw = (
        '{"model": "m", "max_tokens": 8, "messages": [{"role": "user",'
        f' "content": "{DNI}", "content": "hola"}}]}}'
    )
    assert _raw(gateway, path, raw) == 400
    assert upstream_any.requests == []


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("content", "Content"),
        ("messages", "MESSAGES"),
        ("type", "Type"),
        ("content", chr(0xFF43) + "ontent"),  # "c" de ancho completo: NFKC la hace "c"
        ("strasse", "STRASSE"),
        ("stra" + chr(0xDF) + "e", "strasse"),  # casefold: "ß" == "ss"
    ],
)
def test_claves_que_solo_cambian_en_mayusculas_se_rechazan(
    gateway: TestClient, upstream_any: FakeUpstream, first: str, second: str
) -> None:
    """El atacante usa `content` y `Content`: unos lectores ven una clave y otros otra."""
    raw = (
        '{"model": "m", "max_tokens": 8, "messages": [{"role": "user",'
        f' "{first}": "hola", "{second}": "{DNI}"}}]}}'
    )
    assert _raw(gateway, "/v1/chat/completions", raw) == 400
    assert upstream_any.requests == []


def test_clave_duplicada_en_esquema_de_herramienta_se_rechaza(
    gateway: TestClient, upstream_any: FakeUpstream
) -> None:
    """También dentro de `tools`: ningún objeto del cuerpo puede tener claves repetidas."""
    raw = (
        '{"model": "m", "messages": [{"role": "user", "content": "hola"}],'
        ' "tools": [{"type": "function", "function": {"name": "f",'
        ' "parameters": {"type": "object", "properties": {"a": {}, "a": {}}}}}]}'
    )
    assert _raw(gateway, "/v1/chat/completions", raw) == 400
    assert upstream_any.requests == []


def test_claves_distintas_siguen_pasando(gateway: TestClient) -> None:
    raw = (
        '{"model": "m", "max_tokens": 8,'
        ' "messages": [{"role": "user", "content": "hola", "name": "ana_1"}]}'
    )
    assert _raw(gateway, "/v1/chat/completions", raw) == 200


# --- Peticiones desde un navegador -----------------------------------------------------------


@pytest.mark.parametrize("path", PROXY_PATHS)
@pytest.mark.parametrize(
    "origin", ["https://evil.example", "null", "http://localhost:8000", "http://testserver"]
)
def test_peticion_con_origin_se_rechaza(
    gateway: TestClient, upstream_any: FakeUpstream, path: str, origin: str
) -> None:
    """Una web ajena usa la pasarela desde el navegador de un empleado (con la clave)."""
    response = gateway.post(path, json=GATEWAY_BODY, headers={**AUTH, "Origin": origin})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_not_allowed"
    assert origin not in response.text
    assert "access-control-allow-origin" not in response.headers
    assert upstream_any.requests == []


def test_origin_sin_clave_da_401(gateway: TestClient, upstream_any: FakeUpstream) -> None:
    """Primero la clave: sin ella no se da ninguna pista sobre las otras reglas."""
    response = gateway.post(
        "/v1/chat/completions", json=GATEWAY_BODY, headers={"Origin": "https://evil.example"}
    )
    assert response.status_code == 401
    assert upstream_any.requests == []


def test_preflight_cors_no_se_contesta(gateway: TestClient) -> None:
    """Un preflight (OPTIONS) nunca recibe cabeceras CORS: el navegador no deja seguir."""
    response = gateway.options(
        "/v1/chat/completions",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 401
    assert not any(h.startswith("access-control-") for h in response.headers)


def test_origin_configurado_se_acepta_solo_exacto(upstream_any: FakeUpstream) -> None:
    settings = gateway_settings(allowed_origins=["https://intranet.example"])
    for client in gateway_client(upstream_any, settings):
        ok = client.post(
            "/v1/chat/completions",
            json=GATEWAY_BODY,
            headers={**AUTH, "Origin": "https://intranet.example"},
        )
        assert ok.status_code == 200
        assert "access-control-allow-origin" not in ok.headers  # sigue sin haber CORS
        for origin in (
            "https://intranet.example.evil.example",
            "https://INTRANET.example",
            "https://intranet.example/",
            "http://intranet.example",
        ):
            response = client.post(
                "/v1/chat/completions", json=GATEWAY_BODY, headers={**AUTH, "Origin": origin}
            )
            assert response.status_code == 403
    assert len(upstream_any.requests) == 1


def test_dos_cabeceras_origin_se_rechazan(upstream_any: FakeUpstream) -> None:
    """Una permitida y otra no: se rechaza (no se elige cuál mirar)."""
    settings = gateway_settings(allowed_origins=["https://intranet.example"])
    for client in gateway_client(upstream_any, settings):
        response = client.post(
            "/v1/chat/completions",
            json=GATEWAY_BODY,
            headers=[  # type: ignore[arg-type]
                ("Authorization", f"Bearer {GATEWAY_KEY}"),
                ("Origin", "https://intranet.example"),
                ("Origin", "https://evil.example"),
            ],
        )
        assert response.status_code == 403
    assert upstream_any.requests == []


@pytest.mark.parametrize("path", PROXY_PATHS)
@pytest.mark.parametrize(
    "content_type",
    [
        "text/plain",
        "text/plain;charset=UTF-8",
        "application/x-www-form-urlencoded",
        "multipart/form-data; boundary=x",
        "application/jsonx",
        "application/json-patch+json",
        "application/json; charset=latin-1",
        "application/json; charset=utf-8; x=1",
        "text/plain; application/json",
        "",
    ],
)
def test_content_type_que_no_es_json_se_rechaza(
    gateway: TestClient, upstream_any: FakeUpstream, path: str, content_type: str
) -> None:
    """Un formulario o text/plain no necesita preflight en el navegador: se rechaza con 415."""
    raw = '{"model": "m", "max_tokens": 8, "messages": [{"role": "user", "content": "hola"}]}'
    response = gateway.post(
        path, content=raw.encode(), headers={**AUTH, "Content-Type": content_type}
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_media_type"
    assert upstream_any.requests == []


def test_sin_content_type_se_rechaza(gateway: TestClient, upstream_any: FakeUpstream) -> None:
    raw = b'{"model": "m", "messages": [{"role": "user", "content": "hola"}]}'
    response = gateway.post("/v1/chat/completions", content=raw, headers=AUTH)

    assert response.status_code == 415
    assert upstream_any.requests == []


@pytest.mark.parametrize(
    "content_type",
    [
        "application/json",
        "Application/JSON",
        "application/json; charset=utf-8",
        "application/json;charset=UTF-8",
        'application/json; charset="utf-8"',
    ],
)
def test_content_type_json_se_acepta(gateway: TestClient, content_type: str) -> None:
    raw = b'{"model": "m", "messages": [{"role": "user", "content": "hola"}]}'
    response = gateway.post(
        "/v1/chat/completions", content=raw, headers={**AUTH, "Content-Type": content_type}
    )
    assert response.status_code == 200


def test_dos_content_type_se_rechazan(gateway: TestClient, upstream_any: FakeUpstream) -> None:
    raw = b'{"model": "m", "messages": [{"role": "user", "content": "hola"}]}'
    response = gateway.post(
        "/v1/chat/completions",
        content=raw,
        headers=[  # type: ignore[arg-type]
            ("Authorization", f"Bearer {GATEWAY_KEY}"),
            ("Content-Type", "application/json"),
            ("Content-Type", "text/plain"),
        ],
    )
    assert response.status_code == 415
    assert upstream_any.requests == []


# --- Host manipulado ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    [
        "evil.example",
        "testserver.evil.example",
        "evil.example#testserver",
        "testserver@evil.example",
        "",
    ],
)
def test_host_no_permitido_se_rechaza(
    gateway: TestClient, upstream_any: FakeUpstream, host: str
) -> None:
    """DNS rebinding: una web ajena apunta su dominio a la IP de la pasarela."""
    for path in ("/healthz", *PROXY_PATHS):
        response = gateway.post(path, json=GATEWAY_BODY, headers={**AUTH, "Host": host})
        assert response.status_code == 400
    assert upstream_any.requests == []


def test_host_con_puerto_se_acepta(gateway: TestClient) -> None:
    response = gateway.post(
        "/v1/chat/completions", json=GATEWAY_BODY, headers={**AUTH, "Host": "testserver:8000"}
    )
    assert response.status_code == 200


def test_hosts_por_defecto_son_locales(upstream_any: FakeUpstream) -> None:
    settings = gateway_settings(allowed_hosts=Settings.model_fields["allowed_hosts"].default)
    for client in gateway_client(upstream_any, settings):
        for host, status in (
            ("localhost:8000", 200),
            ("127.0.0.1", 200),
            ("[::1]:8000", 200),
            ("testserver", 400),
            ("192.0.2.1", 400),
            ("antifaz", 400),
        ):
            response = client.get("/healthz", headers={"Host": host})
            assert response.status_code == status, host


# --- Arranque seguro -------------------------------------------------------------------------


def test_los_valores_de_env_example_no_arrancan() -> None:
    """Quien copia .env.example sin cambiar nada no obtiene una pasarela con clave conocida."""
    settings = Settings(_env_file=REPO / ".env.example")  # type: ignore[call-arg]

    with pytest.raises(UnsafeConfigError) as info:
        create_app(settings)

    assert settings.antifaz_api_key is not None
    assert settings.antifaz_api_key.get_secret_value() not in str(info.value)


def test_cada_clave_de_env_example_se_rechaza_sola() -> None:
    example = Settings(_env_file=REPO / ".env.example")  # type: ignore[call-arg]
    good = SecretStr(GATEWAY_KEY)
    for field in ("antifaz_api_key", "openai_api_key", "anthropic_api_key"):
        value = getattr(example, field)
        assert value is not None, field
        overrides = {"openai_api_key": None, "anthropic_api_key": None, "antifaz_api_key": good}
        overrides[field] = value
        with pytest.raises(UnsafeConfigError):
            create_app(gateway_settings(**overrides))


@pytest.mark.parametrize(
    "overrides",
    [
        {"antifaz_api_key": None},
        {"antifaz_api_key": SecretStr("clave-corta-de-prueba")},
        {"antifaz_api_key": SecretStr("change-me-" + "a" * 40)},
        {"openai_api_key": SecretStr("change-me-provider-key")},
        {"allowed_hosts": ["*"]},
        {"allowed_origins": ["null"]},
    ],
)
def test_configuracion_abierta_no_arranca(overrides: dict[str, object]) -> None:
    with pytest.raises(UnsafeConfigError):
        create_app(gateway_settings(**overrides))


# --- Trucos de ruta y cabeceras de clave ------------------------------------------------------

PATH_TRICKS = [
    "//v1/chat/completions",
    "/v1/chat/completions/",
    "/v1//chat/completions",
    "/v1/chat%2Fcompletions",
    "/v1/chat/completions%2F",
    "/V1/CHAT/COMPLETIONS",
    "/v1/messages/",
    "/v1/messages/count_tokens/",
    "/healthz/",
    "//healthz",
    "/HEALTHZ",
    "/healthz/../v1/chat/completions",
    "/healthz%2F..%2Fv1%2Fchat%2Fcompletions",
    "/healthz;/v1/chat/completions",
    "/healthz%00",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/",
]


@pytest.mark.parametrize("path", PATH_TRICKS)
@pytest.mark.parametrize("method", ["GET", "HEAD", "POST", "OPTIONS", "PUT", "DELETE"])
def test_trucos_de_ruta_sin_clave_dan_401(
    gateway: TestClient, upstream_any: FakeUpstream, path: str, method: str
) -> None:
    """Variantes de ruta que un router podría tratar como otra: sin clave, nada."""
    response = gateway.request(method, path, json=GATEWAY_BODY if method == "POST" else None)

    if response.request.url.path == "/healthz":  # el cliente HTTP normalizó "../"
        pytest.skip("el cliente normalizó la ruta a /healthz")
    assert response.status_code == 401, response.request.url
    assert upstream_any.requests == []


@pytest.mark.parametrize(
    "path",
    [p for p in PATH_TRICKS if p not in ("/v1/chat%2Fcompletions", "/v1/chat/completions%2F")],
)
def test_trucos_de_ruta_con_clave_no_encuentran_otra_ruta(
    gateway: TestClient, upstream_any: FakeUpstream, path: str
) -> None:
    """Con clave, un alias no redirige (307) ni abre la documentación: 404 o 405."""
    response = gateway.post(path, json=GATEWAY_BODY, headers=AUTH, follow_redirects=False)

    if response.request.url.path in ("/healthz", *PROXY_PATHS):
        pytest.skip("el cliente normalizó la ruta")
    assert response.status_code in (404, 405), path
    assert upstream_any.requests == []


def test_barra_codificada_llega_a_la_ruta_con_clave_y_guardia(
    gateway: TestClient, upstream_any: FakeUpstream
) -> None:
    """`%2F` se decodifica antes de decidir: es la misma ruta, con la misma clave y guardia."""
    response = gateway.post(
        "/v1/chat%2Fcompletions", json={**GATEWAY_BODY, "user": DNI}, headers=AUTH
    )
    assert response.status_code in (200, 404)
    assert_not_sent(upstream_any, DNI)


def test_documentacion_desactivada(gateway: TestClient) -> None:
    for path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
        assert gateway.get(path, headers=AUTH).status_code == 404


@pytest.mark.parametrize(
    "headers",
    [
        [("x-api-key", GATEWAY_KEY), ("x-api-key", "wrong")],
        [("x-api-key", "wrong"), ("x-api-key", GATEWAY_KEY)],
        [("Authorization", f"Bearer {GATEWAY_KEY}"), ("Authorization", "Bearer wrong")],
        [("Authorization", "Bearer wrong"), ("Authorization", f"Bearer {GATEWAY_KEY}")],
        [("Authorization", f"bearer {GATEWAY_KEY}")],
        [("Authorization", f"Bearer  {GATEWAY_KEY}")],
        [("Authorization", f"Bearer {GATEWAY_KEY[:-1]}")],
        [("x-api-key", f" {GATEWAY_KEY}x")],
    ],
)
def test_cabeceras_de_clave_repetidas_o_raras_dan_401(
    gateway: TestClient, upstream_any: FakeUpstream, headers: list[tuple[str, str]]
) -> None:
    """El atacante repite la cabecera de la clave para confundir a quien la lee."""
    response = gateway.post(
        "/v1/messages",
        json=GATEWAY_BODY,
        headers=[*headers, ("Content-Type", "application/json")],  # type: ignore[arg-type]
    )
    assert response.status_code == 401
    assert upstream_any.requests == []


@pytest.mark.parametrize("path", ["/v1/chat/completions", "/healthz", "/ws"])
def test_websocket_se_cierra_siempre(gateway: TestClient, path: str) -> None:
    """No hay rutas WebSocket: cualquier intento se cierra, con o sin clave."""
    for headers in ({}, AUTH):
        with pytest.raises(WebSocketDisconnect), gateway.websocket_connect(path, headers=headers):
            pass  # pragma: no cover - the connection is refused before this line
