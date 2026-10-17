"""Which console messages a browser may log on the panel's pages (PR 11c-2a). No Playwright.

None, with one strict exception: the headless shell of Chromium on Linux does not know the
`bluetooth` feature of the panel's Permissions-Policy header (web/headers.py; the header does
not change) and logs one fixed warning for it. On that platform and only there, that exact text
is allowed, and it MUST appear: the day Chromium learns the feature the test fails and the
exception is removed. Any other message, on any platform, fails.
"""

from collections.abc import Iterable

BLUETOOTH_WARNING = (
    "[warning] Error with Permissions-Policy header: Unrecognized feature: 'bluetooth'."
)
# (browser, platform family) -> the messages it must log, and the only ones it may.
EXPECTED: dict[tuple[str, str], frozenset[str]] = {
    ("chromium", "linux"): frozenset({BLUETOOTH_WARNING}),
}


def _family(sys_platform: str) -> str:
    return "linux" if sys_platform.startswith("linux") else sys_platform


def unexpected_console(messages: Iterable[str], browser: str, sys_platform: str) -> list[str]:
    """What is wrong with the console of a page: every message not allowed, plus a line for each
    expected message that never came (empty when the console is as it must be)."""
    messages = list(messages)
    expected = EXPECTED.get((browser, _family(sys_platform)), frozenset())
    problems = [message for message in messages if message not in expected]
    problems += [f"expected but missing: {m}" for m in sorted(expected - set(messages))]
    return problems
