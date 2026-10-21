"""Which console messages a browser may log on the panel's pages (PR 11c-2a). No Playwright.

None, with two strict exceptions:

- The headless shell of Chromium on Linux does not know the `bluetooth` feature of the panel's
  Permissions-Policy header (web/headers.py; the header does not change) and logs one fixed
  warning for it. On that platform and only there, that exact text is allowed, and it MUST
  appear: the day Chromium learns the feature the test fails and the exception is removed.
- A refused login (PR 11c-2b). Chromium and WebKit log every 4xx answer to a fetch as "Failed to
  load resource". A test that types a wrong token on purpose passes `refused=N`, the number of
  refusals it caused, and the URL each console line came from (`locations`, the same order as
  the messages); only the exact lines for 401 and 429 whose URL's path is /panel/login are then
  allowed, at most N in total. Firefox logs none, so none is required.
- A live stream the test gateway cut on purpose (11e-2, `cut=N`): only Chromium's exact line
  for a broken chunked body, from /panel/api/live, at most N times. A refused stream (a 401, a
  406, the wrong type) is never allowed.

Any other message, on any platform, fails. Also here, `unexpected_failures()`: the failed requests
of a page but the «En directo» GET the page aborted itself, reported with the engine's own abort.
"""

from collections.abc import Iterable, Sequence
from urllib.parse import urlsplit

BLUETOOTH_WARNING = (
    "[warning] Error with Permissions-Policy header: Unrecognized feature: 'bluetooth'."
)
# (browser, platform family) -> the messages it must log, and the only ones it may.
EXPECTED: dict[tuple[str, str], frozenset[str]] = {
    ("chromium", "linux"): frozenset({BLUETOOTH_WARNING}),
}
_FAILED_RESOURCE = "[error] Failed to load resource: the server responded with a status of "
LOGIN_PATH = "/panel/login"
REFUSED_LOGIN = frozenset(
    {_FAILED_RESOURCE + "401 (Unauthorized)", _FAILED_RESOURCE + "429 (Too Many Requests)"}
)
# 11e-2, «En directo». A stream the test gateway cuts mid-way on purpose: Chromium logs this
# line from the live URL (Firefox and WebKit log nothing); allowed at most `cut` times.
LIVE_PATH = "/panel/api/live"
STREAM_CUT = "[error] Failed to load resource: net::ERR_INCOMPLETE_CHUNKED_ENCODING"
# How Chromium and Firefox report that broken stream as a failed request (WebKit: not at all).
STREAM_BROKEN = frozenset({"net::ERR_INCOMPLETE_CHUNKED_ENCODING", "NS_ERROR_NET_PARTIAL_TRANSFER"})
# How each engine reports a request the page aborted itself (its EventSource.close()):
# Chromium, Firefox (two spellings) and WebKit (two spellings).
STREAM_ABORTED = frozenset(
    {
        "net::ERR_ABORTED",
        "NS_BINDING_ABORTED",
        "NS_ERROR_ABORT",
        "Load request cancelled",
        "cancelled",
    }
)


def _family(sys_platform: str) -> str:
    return "linux" if sys_platform.startswith("linux") else sys_platform


def unexpected_console(
    messages: Iterable[str],
    browser: str,
    sys_platform: str,
    locations: Sequence[str] = (),
    *,
    refused: int = 0,
    cut: int = 0,
) -> list[str]:
    """What is wrong with the console of a page: every message not allowed, plus a line for each
    expected message that never came (empty when the console is as it must be). `refused`: how
    many login refusals the test caused on purpose; `locations`: the URL of each message."""
    messages = list(messages)
    expected = EXPECTED.get((browser, _family(sys_platform)), frozenset())
    problems = []
    for index, message in enumerate(messages):
        if message in expected:
            continue
        url = locations[index] if index < len(locations) else ""
        from_login = urlsplit(url).path == LOGIN_PATH
        if message in REFUSED_LOGIN and from_login and refused > 0:
            refused -= 1
            continue
        if message == STREAM_CUT and urlsplit(url).path == LIVE_PATH and cut > 0:
            cut -= 1
            continue
        problems.append(message)
    problems += [f"expected but missing: {m}" for m in sorted(expected - set(messages))]
    return problems


def unexpected_failures(failed: Iterable[str], origin: str, *, cut: int = 0) -> list[str]:
    """Every failed request ("METHOD URL: failure") but the GET of «En directo» that the page
    closed itself (11e-2), reported with the engine's own abort text: live.js closes its
    EventSource on another view, a hidden page or an error. With `cut=N`, also at most N live
    GETs that the test gateway broke on purpose (STREAM_BROKEN). Any other failure of that URL
    is still reported."""
    live = f"GET {origin}{LIVE_PATH}: "
    problems = []
    for line in failed:
        failure = line[len(live) :] if line.startswith(live) else None
        if failure in STREAM_ABORTED:
            continue
        if failure in STREAM_BROKEN and cut > 0:
            cut -= 1
            continue
        problems.append(line)
    return problems
