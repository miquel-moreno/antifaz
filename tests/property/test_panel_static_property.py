"""The panel's static rule holds whatever the path (issue 53, ADR-0018, PR 11c-1).

- Any path under /panel/static/ that is not exactly a manifest path never answers 200, with or
  without a session, and never carries the bytes of a file.
- Each manifest path answers exactly its own bytes.
"""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from antifaz.web.assets import load_assets
from tests.integration.panel_client import LOCAL_COOKIE, LOCAL_URL, log_in, panel_client
from tests.redteam.test_panel_static import _raw_get

ASSETS = load_assets()
P = "/panel/static/"
NAMES = sorted(ASSETS.files)

# Pieces an attacker would mix: real names, separators, dots, encodings, odd characters.
PIECES = st.sampled_from(
    [*NAMES, "/", "\\", ".", "..", "%2e", "%2f", "%5c", "%00", "\x00", ";", "%25", "\uff0e", "A"]
)
paths = st.one_of(
    st.lists(PIECES, min_size=1, max_size=5).map(lambda parts: P + "".join(parts)),
    st.text(max_size=40).map(lambda tail: P + tail),
    st.builds(lambda name, cut: P + name[:cut], st.sampled_from(NAMES), st.integers(0, 40)),
)


@pytest.fixture(scope="module")
def app_and_cookie() -> Iterator[tuple[FastAPI, str]]:
    with panel_client(LOCAL_URL) as client:
        app: FastAPI = client.app  # type: ignore[assignment]
        yield app, f"{LOCAL_COOKIE}={log_in(client)}"


@settings(
    max_examples=300, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(path=paths, with_session=st.booleans())
def test_a_path_outside_the_manifest_is_never_served(
    app_and_cookie: tuple[FastAPI, str], path: str, with_session: bool
) -> None:
    app, cookie = app_and_cookie
    if path in ASSETS.paths:
        return
    status, body = _raw_get(app, path, cookie if with_session else "")

    assert status != 200
    assert not any(asset.body in body for asset in ASSETS.files.values())


@settings(
    max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(name=st.sampled_from(NAMES), with_session=st.booleans())
def test_each_manifest_path_answers_exactly_its_bytes(
    app_and_cookie: tuple[FastAPI, str], name: str, with_session: bool
) -> None:
    app, cookie = app_and_cookie
    status, body = _raw_get(app, P + name, cookie if with_session else "")

    assert status == 200
    assert body == ASSETS.files[name].body
