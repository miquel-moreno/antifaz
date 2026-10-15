"""The panel's CSRF and logout rules hold whatever the request (issue 53, ADR-0018, PR 11b-2b).

Through the whole app, with POST /panel/logout, PUT /panel and DELETE /panel as witnesses of the
routes that change something, Hypothesis mixes the scheme, the cookie (none, a live session of
the request's scheme, a live session of the OTHER scheme under this scheme's name, a closed
one, garbage, the cookie twice), the X-Antifaz-CSRF header (right, another session's, wrong,
twice, not ASCII, missing), Sec-Fetch-Site, Origin and Content-Type. Always:

- 2xx exactly when layer 3 and JSON pass, the session is alive and of the same scheme, and the
  token is right; and then only the logout answers 2xx (PUT and DELETE get 405).
- The answer, in this order of checks: 403 (layer 3 or JSON), 401 (no session), 403 (token).
- A Set-Cookie exactly on a successful logout, and then exactly the expired cookie of the
  scheme (with `Clear-Site-Data: "cache"`); after it, the same cookie is 401 (303 on GET).
- A session made over the other scheme is closed once it is looked up, and never looked up
  when layer 3 or JSON fail.
- Every answer carries the security headers.
"""

import json

from fastapi.testclient import TestClient
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from antifaz.api.app import create_app
from antifaz.web.cookies import cookie_name, expired_cookie
from antifaz.web.headers import SECURITY_HEADERS
from tests.integration.panel_client import ADMIN_TOKEN, HTTPS_URL, LOCAL_URL, panel_settings

BASES = {"https": HTTPS_URL, "http": LOCAL_URL}
OTHER = {"https": "http", "http": "https"}
WITNESSES = [("POST", "/panel/logout"), ("PUT", "/panel"), ("DELETE", "/panel")]
COOKIES = ["none", "live", "other-scheme", "closed", "garbage", "twice"]
TOKENS = ["right", "other-session", "wrong", "twice", "non-ascii", "missing"]
FETCH_SITES = ["same-origin", "cross-site", "same-site", "missing", "twice"]
ORIGINS = ["right", "other-port", "other-scheme", "other-host", "null", "missing"]
CONTENT_TYPES = ["json", "json-utf8", "text/plain", "form", "missing"]


def _login(client: TestClient, base: str, cookie: str | None = None) -> str:
    headers = {
        "Sec-Fetch-Site": "same-origin",
        "Origin": base,
        "Content-Type": "application/json",
    }
    if cookie is not None:
        headers["Cookie"] = cookie
    response = client.post(
        f"{base}/panel/login", content=json.dumps({"token": ADMIN_TOKEN}), headers=headers
    )
    client.cookies.clear()
    assert response.status_code == 200
    return response.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]


def _get_panel(client: TestClient, scheme: str, session_id: str) -> int:
    response = client.get(
        f"{BASES[scheme]}/panel", headers={"Cookie": f"{cookie_name(scheme)}={session_id}"}
    )
    return response.status_code


def _token(client: TestClient, scheme: str, session_id: str) -> str:
    page = client.get(
        f"{BASES[scheme]}/panel", headers={"Cookie": f"{cookie_name(scheme)}={session_id}"}
    )
    assert page.status_code == 200
    return page.text.split('name="antifaz-csrf" content="', 1)[1].split('"', 1)[0]


def _logout_right(client: TestClient, scheme: str, session_id: str, token: str) -> int:
    base = BASES[scheme]
    response = client.post(
        f"{base}/panel/logout",
        content=b"{}",
        headers={
            "Sec-Fetch-Site": "same-origin",
            "Origin": base,
            "Content-Type": "application/json",
            "Cookie": f"{cookie_name(scheme)}={session_id}",
            "X-Antifaz-CSRF": token,
        },
    )
    client.cookies.clear()
    return response.status_code


requests = st.tuples(
    st.sampled_from(["https", "http"]),
    st.sampled_from(WITNESSES),
    st.sampled_from(COOKIES),
    st.sampled_from(TOKENS),
    st.sampled_from(FETCH_SITES),
    st.sampled_from(ORIGINS),
    st.sampled_from(CONTENT_TYPES),
)
ALL_RIGHT = ("live", "right", "same-origin", "right", "json")


@settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@example(("https", ("POST", "/panel/logout"), *ALL_RIGHT))
@example(("http", ("POST", "/panel/logout"), *ALL_RIGHT))
@example(("https", ("PUT", "/panel"), *ALL_RIGHT))
@example(("http", ("DELETE", "/panel"), *ALL_RIGHT))
@example(
    ("https", ("POST", "/panel/logout"), "other-scheme", "right", "same-origin", "right", "json")
)
@example(
    ("http", ("POST", "/panel/logout"), "other-scheme", "right", "same-origin", "right", "json")
)
@example(
    ("https", ("POST", "/panel/logout"), "other-scheme", "right", "cross-site", "right", "json")
)
@example(
    ("http", ("POST", "/panel/logout"), "live", "other-session", "same-origin", "right", "json")
)
@example(("https", ("POST", "/panel/logout"), "live", "right", "same-origin", "right", "form"))
@given(requests)
def test_only_every_layer_and_a_live_session_of_the_scheme_change_anything(
    sent: tuple[str, tuple[str, str], str, str, str, str, str],
) -> None:
    scheme, (method, path), cookie, token, fetch_site, origin, content_type = sent
    other = OTHER[scheme]
    base = BASES[scheme]
    client = TestClient(create_app(panel_settings()), base_url=HTTPS_URL, follow_redirects=False)
    mine = _login(client, base)
    second = _login(client, base)  # another live session of the same scheme
    foreign = _login(client, BASES[other])  # a live session made over the other scheme
    closed = _login(client, base)
    _login(client, base, cookie=f"{cookie_name(scheme)}={closed}")  # replaces (closes) it
    tokens = {"mine": _token(client, scheme, mine), "second": _token(client, scheme, second)}

    name = cookie_name(scheme)
    values = {"live": mine, "other-scheme": foreign, "closed": closed, "garbage": "x!y"}
    headers: list[tuple[str, bytes]] = []
    if cookie == "twice":
        headers.append(("Cookie", f"{name}={mine}; {name}={mine}".encode()))
    elif cookie != "none":
        headers.append(("Cookie", f"{name}={values[cookie]}".encode()))
    right = tokens["mine"].encode()
    token_lines = {
        "right": [right],
        "other-session": [tokens["second"].encode()],
        "wrong": [b"W" * len(right)],
        "twice": [right, right],
        "non-ascii": [right[:-2] + "ñ".encode()],
        "missing": [],
    }
    headers += [("X-Antifaz-CSRF", value) for value in token_lines[token]]
    sites = {"missing": [], "twice": [b"same-origin", b"same-origin"]}
    headers += [("Sec-Fetch-Site", v) for v in sites.get(fetch_site, [fetch_site.encode()])]
    origins = {
        "right": base,
        "other-port": f"{base}:8443",
        "other-scheme": BASES[other],
        "other-host": "https://evil.example",
        "null": "null",
    }
    if origin != "missing":
        headers.append(("Origin", origins[origin].encode()))
    types = {
        "json": "application/json",
        "json-utf8": "application/json; charset=utf-8",
        "text/plain": "text/plain",
        "form": "application/x-www-form-urlencoded",
    }
    if content_type != "missing":
        headers.append(("Content-Type", types[content_type].encode()))

    response = client.request(method, f"{base}{path}", content=b"{}", headers=headers)
    client.cookies.clear()

    for header, value in SECURITY_HEADERS:
        assert response.headers.get_list(header) == [value], header
    assert not [k for k in response.headers if k.startswith("access-control-")]
    layer_3 = (
        fetch_site == "same-origin" and origin == "right" and content_type in ("json", "json-utf8")
    )
    if not layer_3:
        expected = 403
    elif cookie != "live":
        expected = 401
    elif token != "right":
        expected = 403
    else:
        expected = 200 if path == "/panel/logout" else 405
    assert response.status_code == expected
    if expected == 403:
        assert response.json()["error"]["code"] == "csrf_rejected"
    success = response.is_success
    assert success == (layer_3 and cookie == "live" and token == "right" and method == "POST")
    assert ("set-cookie" in response.headers) == success
    if success:
        assert response.headers.get_list("set-cookie") == [expired_cookie(scheme)]
        assert response.headers.get_list("clear-site-data") == ['"cache"']
        assert _logout_right(client, scheme, mine, tokens["mine"]) == 401
        assert _get_panel(client, scheme, mine) == 303
    else:
        assert "clear-site-data" not in response.headers
        assert _get_panel(client, scheme, mine) == 200  # nothing closed it
    # The other scheme's session: closed once looked up over this scheme, else untouched.
    looked_up_foreign = layer_3 and cookie == "other-scheme"
    assert _get_panel(client, other, foreign) == (303 if looked_up_foreign else 200)
