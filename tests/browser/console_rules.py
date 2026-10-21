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

Any other message, on any platform, fails.
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
        problems.append(message)
    problems += [f"expected but missing: {m}" for m in sorted(expected - set(messages))]
    return problems


def unexpected_failures(failed: Iterable[str], origin: str) -> list[str]:
    raise NotImplementedError
