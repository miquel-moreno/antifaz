"""The pure pieces of the panel door (issue 53, ADR-0018, PR 11b-2a).

- is_panel_path(): which paths are the panel's, on the decoded path.
- cookies.py: the session cookie BY SCHEME (ADR-0018, addendum of 2026-10-13).
- headers.py: the exact security headers of every /panel* answer.
- pages.py: the three fixed HTML pages, which the CSP allows (no inline script or style).
- csrf.py: layer 3 (Sec-Fetch-Site and Origin), the JSON content type and, since 11b-2b,
  layer 2 (the X-Antifaz-CSRF token).
- cookies.py since 11b-2b: the expired cookie of each scheme and the scheme of a session.
- AppError headers: Retry-After on a 429, from the handler and from the gate.
"""

import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from antifaz.api.errors import register_error_handlers
from antifaz.api.gate import GateMiddleware, is_panel_path
from antifaz.http_errors import (
    AppError,
    CsrfRejectedError,
    PanelInternalError,
    PanelLoginFailedError,
    PanelNotFoundError,
    PanelUnauthorizedError,
    TooManyAttemptsError,
    error_response,
)
from antifaz.web.cookies import (
    LOCAL_COOKIE,
    SECURE_COOKIE,
    cookie_name,
    expired_cookie,
    read_session_cookie,
    session_cookie,
    session_scheme,
)
from antifaz.web.csrf import counts_as_use, has_csrf_token, is_json, is_same_origin
from antifaz.web.headers import CONTENT_SECURITY_POLICY, SECURITY_HEADERS, with_security_headers
from antifaz.web.pages import CSRF_META, HTTPS_ONLY_PAGE, LOGIN_PAGE, panel_page
from antifaz.web.sessions import MAX_AGE_SECONDS
from tests.integration.fakes import GATEWAY_KEY

SESSION_ID = "s" * 43

# --- is_panel_path --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["/panel", "/panel/", "/panel/login", "/panel/login/", "/panel/x/y"]
)
def test_the_panel_is_panel_and_everything_under_it(path: str) -> None:
    assert is_panel_path(path)


@pytest.mark.parametrize(
    "path", ["/PANEL", "//panel", "/panelx", "/Panel/login", "/v1/panel", "/", "", "panel", "/pane"]
)
def test_lookalikes_are_not_the_panel(path: str) -> None:
    assert not is_panel_path(path)


# --- cookies by scheme ----------------------------------------------------------------------


def _cookie_headers(*lines: str) -> list[tuple[bytes, bytes]]:
    return [(b"host", b"localhost"), *((b"cookie", line.encode("latin-1")) for line in lines)]


def test_each_scheme_has_its_own_cookie_name() -> None:
    assert cookie_name("https") == SECURE_COOKIE == "__Host-antifaz_session"
    assert cookie_name("http") == LOCAL_COOKIE == "antifaz_session"


def test_the_https_cookie_is_host_prefixed_secure_strict_and_for_the_whole_site() -> None:
    header = session_cookie("https", SESSION_ID)

    assert header == (
        f"__Host-antifaz_session={SESSION_ID}; Max-Age={MAX_AGE_SECONDS}; Path=/; Secure; "
        "HttpOnly; SameSite=Strict"
    )
    assert "domain" not in header.lower()


def test_the_localhost_cookie_has_no_secure_and_only_the_panel_path() -> None:
    header = session_cookie("http", SESSION_ID)

    assert header == (
        f"antifaz_session={SESSION_ID}; Max-Age={MAX_AGE_SECONDS}; Path=/panel; HttpOnly; "
        "SameSite=Strict"
    )
    assert "secure" not in header.lower() and "domain" not in header.lower()
    assert MAX_AGE_SECONDS == 28800


def test_the_expired_https_cookie_has_the_same_name_and_attributes() -> None:
    assert expired_cookie("https") == (
        "__Host-antifaz_session=; Max-Age=0; Path=/; Secure; HttpOnly; SameSite=Strict"
    )


def test_the_expired_localhost_cookie_has_the_same_name_and_attributes() -> None:
    assert (
        expired_cookie("http")
        == "antifaz_session=; Max-Age=0; Path=/panel; HttpOnly; SameSite=Strict"
    )


@pytest.mark.parametrize("scheme", ["https", "http"])
def test_the_expired_cookie_differs_from_the_live_one_only_in_value_and_age(scheme: str) -> None:
    live = session_cookie(scheme, SESSION_ID).split("; ")
    expired = expired_cookie(scheme).split("; ")

    assert live[0] == f"{cookie_name(scheme)}={SESSION_ID}"
    assert expired[0] == f"{cookie_name(scheme)}="
    assert (live[1], expired[1]) == (f"Max-Age={MAX_AGE_SECONDS}", "Max-Age=0")
    assert live[2:] == expired[2:]
    assert "domain" not in expired_cookie(scheme).lower()


@pytest.mark.parametrize(
    ("scheme", "expected"), [("https", "https"), ("http", "http-local"), ("", "http-local")]
)
def test_a_session_is_bound_to_https_only_over_https(scheme: str, expected: str) -> None:
    assert session_scheme(scheme) == expected


@pytest.mark.parametrize("scheme", ["https", "http"])
def test_the_cookie_of_the_scheme_is_read(scheme: str) -> None:
    line = f"other=1; {cookie_name(scheme)}={SESSION_ID}; more=2"

    assert read_session_cookie(_cookie_headers(line), scheme) == SESSION_ID


def test_the_cookie_of_the_other_scheme_is_never_read() -> None:
    assert read_session_cookie(_cookie_headers(f"antifaz_session={SESSION_ID}"), "https") is None
    assert (
        read_session_cookie(_cookie_headers(f"__Host-antifaz_session={SESSION_ID}"), "http") is None
    )


@pytest.mark.parametrize(
    "name",
    ["__host-antifaz_session", "__HOST-antifaz_session", "Antifaz_session", "antifaz_session "],
)
def test_the_name_is_exact_and_case_sensitive(name: str) -> None:
    for scheme in ("https", "http"):
        line = f"{name}={SESSION_ID}"
        if name.strip() == cookie_name(scheme):
            continue  # surrounding spaces are not part of the name
        assert read_session_cookie(_cookie_headers(line), scheme) is None


def test_cookie_lines_split_over_several_headers_are_joined() -> None:
    headers = _cookie_headers("a=1", f"antifaz_session={SESSION_ID}", "b=2")

    assert read_session_cookie(headers, "http") == SESSION_ID


@pytest.mark.parametrize(
    "lines",
    [
        (f"antifaz_session={SESSION_ID}; antifaz_session={SESSION_ID}",),
        (f"antifaz_session={SESSION_ID}", f"antifaz_session={'t' * 43}"),
        (f"antifaz_session={SESSION_ID};antifaz_session=",),
    ],
)
def test_the_cookie_twice_is_no_session(lines: tuple[str, ...]) -> None:
    assert read_session_cookie(_cookie_headers(*lines), "http") is None


@pytest.mark.parametrize("value", ["", '"quoted"', "a b", "a,b", "é" * 4, "x" * 200, "abc\\def"])
def test_a_value_that_is_not_a_session_id_is_no_session(value: str) -> None:
    line = f"antifaz_session={value}"

    assert read_session_cookie(_cookie_headers(line), "http") is None


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (f'antifaz_session="{SESSION_ID}"', None),  # quoted: not an id
        (f"antifaz_session={SESSION_ID},x", None),  # a comma is not part of an id
        (f"antifaz_session = {SESSION_ID}", None),  # spaces around "=": another name
        (f"antifaz_session= {SESSION_ID}", None),  # a space before the value
        (f"antifaz_session={SESSION_ID}\t", SESSION_ID),  # trailing whitespace is dropped
        (f" \tantifaz_session={SESSION_ID} ", SESSION_ID),  # and leading too
        (f"antifaz_session={SESSION_ID}=", None),  # "=" is not part of an id
    ],
)
def test_cookie_value_variants_are_pinned(line: str, expected: str | None) -> None:
    assert read_session_cookie(_cookie_headers(line), "http") == expected


def test_no_cookie_header_is_no_session() -> None:
    assert read_session_cookie(_cookie_headers(), "https") is None
    assert read_session_cookie(_cookie_headers("antifaz_session"), "http") is None


# --- security headers -----------------------------------------------------------------------


def test_the_csp_is_exactly_the_one_of_the_adr() -> None:
    assert CONTENT_SECURITY_POLICY == (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "font-src 'self'; connect-src 'self'; manifest-src 'self'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    )
    for loose in ("unsafe-inline", "unsafe-eval", "data:", "*"):
        assert loose not in CONTENT_SECURITY_POLICY


def test_every_adr_header_is_there_with_its_value() -> None:
    headers = dict(SECURITY_HEADERS)

    assert headers["Content-Security-Policy"] == CONTENT_SECURITY_POLICY
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert headers["Cross-Origin-Resource-Policy"] == "same-origin"
    assert headers["Cache-Control"] == "no-store"
    policy = headers["Permissions-Policy"]
    for feature in (
        "camera",
        "microphone",
        "geolocation",
        "payment",
        "usb",
        "bluetooth",
        "hid",
        "serial",
        "screen-wake-lock",
        "publickey-credentials-get",
        "xr-spatial-tracking",
    ):
        assert f"{feature}=()" in policy
    assert re.fullmatch(r"[a-z-]+=\(\)(, [a-z-]+=\(\))*", policy)


def test_with_security_headers_replaces_the_same_names_and_keeps_the_rest() -> None:
    raw = [(b"cache-control", b"public, max-age=60"), (b"content-type", b"text/html")]

    out = with_security_headers(raw)

    names = [name for name, _ in out]
    assert names.count(b"cache-control") == 1
    assert (b"cache-control", b"no-store") in out
    assert (b"content-type", b"text/html") in out
    assert len(out) == len(SECURITY_HEADERS) + 1


# --- pages ----------------------------------------------------------------------------------

_INLINE = re.compile(r"<script|<style|\sstyle\s*=|\son[a-z]+\s*=", re.IGNORECASE)


@pytest.mark.parametrize("page", [LOGIN_PAGE, HTTPS_ONLY_PAGE, panel_page("csrf-token-x")])
def test_pages_hold_nothing_the_csp_would_block(page: str) -> None:
    assert page.startswith("<!doctype html>")
    assert '<html lang="es">' in page
    assert '<meta charset="utf-8">' in page
    assert not _INLINE.search(page)
    assert "data:" not in page and "http://" not in page and "https://" not in page


def test_the_login_page_says_the_interface_comes_later() -> None:
    assert "11c" in LOGIN_PAGE
    assert CSRF_META not in LOGIN_PAGE


def test_the_https_page_explains_how_to_fix_it_and_offers_no_login() -> None:
    assert "HTTPS" in HTTPS_ONLY_PAGE and "localhost" in HTTPS_ONLY_PAGE
    assert "<form" not in HTTPS_ONLY_PAGE and "<input" not in HTTPS_ONLY_PAGE


def test_the_panel_page_carries_the_csrf_token_in_its_meta_only() -> None:
    page = panel_page("csrf-token-x")

    assert f'<meta name="{CSRF_META}" content="csrf-token-x">' in page
    assert page.count("csrf-token-x") == 1
    assert CSRF_META == "antifaz-csrf"


def test_the_panel_page_escapes_the_token() -> None:
    page = panel_page('"><b>')

    assert '"><b>' not in page
    assert "&quot;&gt;&lt;b&gt;" in page


# --- CSRF layer 3 and JSON ------------------------------------------------------------------


def _headers(**values: str | list[str]) -> list[tuple[bytes, bytes]]:
    out: list[tuple[bytes, bytes]] = []
    for name, value in values.items():
        for item in value if isinstance(value, list) else [value]:
            out.append((name.replace("_", "-").encode(), item.encode("utf-8")))
    return out


def _same_origin(**values: str | list[str]) -> list[tuple[bytes, bytes]]:
    base: dict[str, str | list[str]] = {
        "sec_fetch_site": "same-origin",
        "origin": "https://antifaz.example",
    }
    return _headers(**{**base, **values})


def test_same_origin_with_the_exact_origin_passes() -> None:
    assert is_same_origin(_same_origin(), "https", "antifaz.example")
    local = _same_origin(origin="http://localhost:8000")
    assert is_same_origin(local, "http", "localhost:8000")


@pytest.mark.parametrize(
    "changes",
    [
        {"sec_fetch_site": "cross-site"},
        {"sec_fetch_site": "same-site"},
        {"sec_fetch_site": "none"},
        {"sec_fetch_site": "Same-Origin"},
        {"sec_fetch_site": ["same-origin", "same-origin"]},
        {"origin": "null"},
        {"origin": "http://antifaz.example"},
        {"origin": "https://antifaz.example:8443"},
        {"origin": "https://antifaz.example/"},
        {"origin": "https://ANTIFAZ.example"},
        {"origin": "https://evil.example"},
        {"origin": ["https://antifaz.example", "https://antifaz.example"]},
        {"origin": "https://antifaz.éxample"},
        {"origin": "https://antifaz.example\u0000"},
    ],
)
def test_anything_else_is_not_same_origin(changes: dict[str, str | list[str]]) -> None:
    assert not is_same_origin(_same_origin(**changes), "https", "antifaz.example")


def test_a_missing_header_or_host_is_not_same_origin() -> None:
    assert not is_same_origin(
        _headers(origin="https://antifaz.example"), "https", "antifaz.example"
    )
    assert not is_same_origin(_headers(sec_fetch_site="same-origin"), "https", "antifaz.example")
    assert not is_same_origin(_same_origin(), "https", None)
    assert not is_same_origin(_same_origin(origin="https://"), "https", "")


@pytest.mark.parametrize(
    "value", ["application/json", "application/json; charset=utf-8", "Application/JSON"]
)
def test_json_content_types_pass(value: str) -> None:
    assert is_json(_headers(content_type=value))


@pytest.mark.parametrize(
    "value",
    [
        "text/plain",
        "multipart/form-data; boundary=x",
        "application/x-www-form-urlencoded",
        "application/json; charset=latin-1",
        "application/json-patch+json",
        ["application/json", "application/json"],
    ],
)
def test_other_content_types_do_not(value: str | list[str]) -> None:
    assert not is_json(_headers(content_type=value))
    assert not is_json(_headers())


# --- CSRF layer 2: the session's token ----------------------------------------------------

TOKEN = "t0k3n-of-the-session-for-tests-only_abcdefghijk"


def _token_headers(*values: bytes) -> list[tuple[bytes, bytes]]:
    return [(b"host", b"localhost"), *((b"X-Antifaz-CSRF", value) for value in values)]


def test_the_right_token_passes() -> None:
    assert has_csrf_token(_token_headers(TOKEN.encode()), TOKEN)
    assert has_csrf_token([(b"x-antifaz-csrf", TOKEN.encode())], TOKEN)  # any case of the name


@pytest.mark.parametrize(
    "values",
    [
        (),  # missing
        (b"",),  # empty
        (TOKEN.encode(), TOKEN.encode()),  # duplicated, even both right
        (TOKEN.encode(), b"other"),
        (b"other-session-token-abcdefghijklmnopqrstuvwxyz0123",),  # another session's
        (TOKEN[:-1].encode(),),  # one shorter
        ((TOKEN + "x").encode(),),  # one longer
        (TOKEN.upper().encode(),),  # case changed
        (TOKEN.encode() + b" ",),  # trailing space
        (b" " + TOKEN.encode(),),
        ("t0k3n-\u00f1".encode(),),  # not ASCII
        (TOKEN[:-1].encode() + b"\xff",),  # not ASCII, same length
    ],
)
def test_anything_but_exactly_one_right_token_fails(values: tuple[bytes, ...]) -> None:
    assert not has_csrf_token(_token_headers(*values), TOKEN)


def test_a_header_of_another_name_is_no_token() -> None:
    headers = [(b"x-csrf-token", TOKEN.encode()), (b"x-antifaz-csrf-token", TOKEN.encode())]

    assert not has_csrf_token(headers, TOKEN)


@pytest.mark.parametrize("expected", ["", "\u00f1" * 10])
def test_an_empty_or_non_ascii_expected_token_never_matches(expected: str) -> None:
    assert not has_csrf_token(_token_headers(expected.encode("utf-8")), expected)
    # With a header that passes its own checks, the expected value alone must refuse it.
    assert not has_csrf_token(_token_headers(b"any-ascii-token"), expected)


@pytest.mark.parametrize("expected", ["", "t\u00f1", "\u00f1" * 43])
def test_an_ascii_header_against_an_empty_or_non_ascii_expected_is_false(expected: str) -> None:
    assert not has_csrf_token(_token_headers(TOKEN.encode()), expected)
    assert not has_csrf_token(_token_headers(b"t?"), expected)


# --- counts_as_use: which requests may refresh a session's last use ------------------------


@pytest.mark.parametrize(
    "values", [[], [b"same-origin"], [b"none"]], ids=["absent", "same-origin", "none"]
)
def test_the_panel_page_or_no_browser_counts_as_use(values: list[bytes]) -> None:
    headers = [(b"host", b"localhost"), *((b"sec-fetch-site", v) for v in values)]

    assert counts_as_use(headers)


@pytest.mark.parametrize(
    "values",
    [
        [b"same-site"],  # another localhost port: it carries the cookie
        [b"cross-site"],
        [b"Same-Origin"],
        [b""],
        [b"same-origin", b"same-origin"],
        [b"same-origin", b"cross-site"],
    ],
)
def test_anything_else_does_not_count_as_use(values: list[bytes]) -> None:
    headers = [(b"Sec-Fetch-Site", v) for v in values]

    assert not counts_as_use(headers)


def test_the_token_is_compared_in_constant_time(monkeypatch: pytest.MonkeyPatch) -> None:
    import hmac

    from antifaz.web import csrf

    calls: list[tuple[bytes, bytes]] = []
    real = hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(csrf.hmac, "compare_digest", spy)

    assert has_csrf_token(_token_headers(TOKEN.encode()), TOKEN)
    assert calls == [(TOKEN.encode(), TOKEN.encode())]


# --- AppError headers -----------------------------------------------------------------------


def test_app_errors_have_no_headers_by_default() -> None:
    assert AppError("x").headers == {}
    assert PanelLoginFailedError().headers == {}


def test_too_many_attempts_carries_retry_after() -> None:
    error = TooManyAttemptsError(37)

    assert error.status_code == 429
    assert error.headers == {"Retry-After": "37"}
    response = error_response(error)
    assert response.headers["retry-after"] == "37"
    assert response.status_code == 429


@pytest.mark.parametrize(
    ("error", "status", "body"),
    [
        (PanelNotFoundError(), 404, b'{"error":{"code":"not_found","message":"no such route"}}'),
        (
            PanelUnauthorizedError(),
            401,
            b'{"error":{"code":"panel_unauthorized","message":"log in to the panel first"}}',
        ),
        (
            PanelLoginFailedError(),
            401,
            b'{"error":{"code":"login_failed","message":"login failed"}}',
        ),
        (
            CsrfRejectedError(),
            403,
            b'{"error":{"code":"csrf_rejected","message":"request refused: it must come from '
            b"the panel's own page, as JSON\"}}",
        ),
        (
            PanelInternalError(),
            500,
            b'{"error":{"code":"internal_error","message":"internal error"}}',
        ),
    ],
)
def test_panel_errors_have_fixed_status_and_body(error: AppError, status: int, body: bytes) -> None:
    response = error_response(error)

    assert response.status_code == status
    assert response.body == body


def test_the_handler_sends_the_error_headers() -> None:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/x")
    async def boom() -> None:
        raise TooManyAttemptsError(5)

    response = TestClient(app).get("/x")

    assert response.status_code == 429
    assert response.headers["retry-after"] == "5"
    assert response.json()["error"]["code"] == "too_many_attempts"


def test_the_gate_sends_the_error_headers() -> None:
    class TeapotError(AppError):
        status_code = 418
        code = "teapot"

    async def inner(scope: object, receive: object, send: object) -> None:
        raise AssertionError("never reached")

    gate = GateMiddleware(inner, api_key=GATEWAY_KEY, allowed_origins=[])
    gate._problem = lambda scope: TeapotError("x", headers={"Retry-After": "9"})  # type: ignore[method-assign]

    response = TestClient(gate).get("/v1/models")

    assert response.status_code == 418
    assert response.headers["retry-after"] == "9"


def test_the_gate_without_a_panel_door_answers_404_to_every_panel_path() -> None:
    async def inner(scope: object, receive: object, send: object) -> None:
        raise AssertionError("never reached")

    client = TestClient(GateMiddleware(inner, api_key=GATEWAY_KEY, allowed_origins=[]))
    for path in ("/panel", "/panel/login", "/panel/x"):
        response = client.get(path)
        assert response.status_code == 404
        assert response.json() == {"error": {"code": "not_found", "message": "no such route"}}
