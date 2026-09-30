"""Shared pieces of the proxy tests: a fake upstream (httpx.MockTransport) and test keys."""

from collections.abc import Callable, Sequence

import httpx

from antifaz import Span
from antifaz.detect.scan import scan
from tests.conftest import SENTINEL_DNI

# Obviously fake keys, only for tests.
GATEWAY_KEY = "test-gateway-key-not-real"
PROVIDER_KEY = "test-provider-key-not-real"

Handler = Callable[[httpx.Request], httpx.Response]


class FakeUpstream:
    """Records every request and answers with the given handler."""

    def __init__(self, handler: Handler) -> None:
        self.requests: list[httpx.Request] = []
        self.handler = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


def raiser(error: type[httpx.HTTPError]) -> Handler:
    """A handler that fails with `error`, whose message carries the sentinel."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise error(f"boom {SENTINEL_DNI}", request=request)  # type: ignore[call-arg]

    return handler


def misses_repeats(text: str) -> Sequence[Span]:
    """A detector that only reports the first appearance: the guard must catch the rest."""
    return scan(text)[:1]


def compact(text: str) -> str:
    return "".join(c for c in text.casefold() if c.isalnum())
