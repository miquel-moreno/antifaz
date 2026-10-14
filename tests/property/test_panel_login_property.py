"""The panel door holds its rules whatever the request (issue 53, ADR-0018, PR 11b-2a).

- is_panel_path() is true exactly for "/panel" and paths whose first segment is "panel".
- The cookie parser never returns the value of another name or of the other scheme's cookie,
  and returns nothing when the cookie of the scheme comes more than once.
- The Origin check accepts exactly the origin "scheme://host".
- Through the whole app: a session route never answers 2xx without exactly one valid cookie of
  the request's scheme holding a session made over that scheme; a cookie is only ever set by a
  successful login; and every /panel* answer carries the ADR's security headers.
"""

import json

from fastapi.testclient import TestClient
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from antifaz.api.app import create_app
from antifaz.api.gate import is_panel_path
from antifaz.web.cookies import cookie_name, read_session_cookie
from antifaz.web.csrf import is_same_origin
from antifaz.web.headers import SECURITY_HEADERS
from tests.integration.panel_client import (
    ADMIN_TOKEN,
    HTTPS_URL,
    LOCAL_COOKIE,
    LOCAL_URL,
    SECURE_COOKIE,
    panel_settings,
)

# --- is_panel_path --------------------------------------------------------------------------

segments = st.sampled_from(["panel", "PANEL", "panelx", "", "login", "v1", "..", "x", "Panel"])
paths = st.lists(segments, max_size=5).map(lambda parts: "/" + "/".join(parts)) | st.text(
    max_size=20
)


@given(paths)
def test_is_panel_path_iff_the_first_segment_is_panel(path: str) -> None:
    assert is_panel_path(path) == (path.split("/", 2)[:2] == ["", "panel"])


# --- Cookie parser --------------------------------------------------------------------------

NAMES = [SECURE_COOKIE, LOCAL_COOKIE, "__host-antifaz_session", "Antifaz_session", "theme", "a"]
ids = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij0123456789-_", min_size=1, max_size=50)
pairs = st.lists(st.tuples(st.sampled_from(NAMES), ids), max_size=6)


@given(pairs, st.sampled_from(["https", "http"]), st.integers(min_value=1, max_value=3))
def test_the_parser_returns_only_the_one_cookie_of_the_scheme(
    cookies: list[tuple[str, str]], scheme: str, lines: int
) -> None:
    texts = [f"{name}={value}" for name, value in cookies]
    split = [texts[i::lines] for i in range(lines)]
    headers = [(b"cookie", "; ".join(part).encode()) for part in split if part]

    result = read_session_cookie(headers, scheme)

    mine = [value for name, value in cookies if name == cookie_name(scheme)]
    assert result == (mine[0] if len(mine) == 1 else None)


# --- Origin ---------------------------------------------------------------------------------

hosts = st.sampled_from(["localhost", "localhost:8000", "127.0.0.1", "[::1]", "antifaz.example"])
origins = st.builds(
    lambda scheme, host: f"{scheme}://{host}", st.sampled_from(["http", "https", "HTTPS"]), hosts
) | st.text(max_size=30)


@given(origins, st.sampled_from(["http", "https"]), hosts)
def test_the_origin_check_accepts_exactly_scheme_and_host(
    origin: str, scheme: str, host: str
) -> None:
    headers = [(b"sec-fetch-site", b"same-origin"), (b"origin", origin.encode("utf-8"))]

    assert is_same_origin(headers, scheme, host) == (origin == f"{scheme}://{host}")


# --- The whole door -------------------------------------------------------------------------

# The session routes this test can open: it sends no X-Antifaz-CSRF, so POST /panel/logout
# (which needs it) is the witness of tests/property/test_panel_logout_property.py instead.
SESSION_ROUTES = {("/panel", "GET")}
SUCCESSFUL_LOGIN = ("/panel/login", "POST", "right", True)


def _fresh_door() -> TestClient:
    """A client on a new app: new sessions and a new limiter, so earlier examples (failed
    logins, sessions closed by a later login) never change what this one sees."""
    return TestClient(create_app(panel_settings()), base_url=HTTPS_URL, follow_redirects=False)


def _session_id(client: TestClient, base: str) -> str:
    response = client.post(
        f"{base}/panel/login",
        content=json.dumps({"token": ADMIN_TOKEN}),
        headers={
            "Sec-Fetch-Site": "same-origin",
            "Origin": base,
            "Content-Type": "application/json",
        },
    )
    client.cookies.clear()
    assert response.status_code == 200
    return response.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]


cookie_parts = st.lists(
    st.tuples(
        st.sampled_from([SECURE_COOKIE, LOCAL_COOKIE, "__host-antifaz_session", "x"]),
        st.sampled_from(["valid-https", "valid-http", "junk", ""]),
    ),
    max_size=3,
)
requests = st.tuples(
    st.sampled_from(["https", "http"]),
    st.sampled_from(["/panel", "/panel/", "/panel/login", "/panel/login/", "/panel/x"]),
    st.sampled_from(["GET", "POST", "HEAD", "PUT"]),
    cookie_parts,
    st.sampled_from(["right", "wrong", "garbage"]),
    st.booleans(),
)


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@example(("https", "/panel/login", "POST", [], "right", True))
@example(("http", "/panel/login", "POST", [(LOCAL_COOKIE, "valid-http")], "right", True))
@example(("https", "/panel", "GET", [(SECURE_COOKIE, "valid-https")], "wrong", False))
@example(("http", "/panel", "GET", [(LOCAL_COOKIE, "valid-http")], "wrong", False))
@given(requests)
def test_the_door_never_lets_in_without_a_session_and_always_sends_the_headers(
    sent: tuple[str, str, str, list[tuple[str, str]], str, bool],
) -> None:
    client = _fresh_door()
    valid = {"https": _session_id(client, HTTPS_URL), "http": _session_id(client, LOCAL_URL)}
    scheme, path, method, cookies, token, same_origin = sent
    base = HTTPS_URL if scheme == "https" else LOCAL_URL
    values = {"valid-https": valid["https"], "valid-http": valid["http"], "junk": "junk", "": ""}
    cookie = "; ".join(f"{name}={values[value]}" for name, value in cookies)
    body = {"right": json.dumps({"token": ADMIN_TOKEN}), "wrong": '{"token": "no"}'}
    headers = {"Content-Type": "application/json", "Sec-Fetch-Site": "same-origin"}
    if same_origin:
        headers["Origin"] = base
    if cookie:
        headers["Cookie"] = cookie

    response = client.request(
        method, f"{base}{path}", content=body.get(token, "{"), headers=headers
    )

    for name, value in SECURITY_HEADERS:
        assert response.headers.get_list(name) == [value], name
    # A cookie is set exactly by a successful login (the limiter is fresh: it always succeeds).
    successful = (path, method, token, same_origin) == SUCCESSFUL_LOGIN
    assert ("set-cookie" in response.headers) == successful
    if successful:
        assert response.status_code == 200
    mine = [value for name, value in cookies if name == cookie_name(scheme)]
    if (path, method) in SESSION_ROUTES:
        # 2xx exactly with one cookie of the request's scheme NAME holding a live session id
        # made over that same scheme (11b-2b): the cookie name picks what is read, and the
        # store refuses (and closes) a session made over the other scheme.
        live = len(mine) == 1 and mine[0] == f"valid-{scheme}"
        assert response.is_success == live
