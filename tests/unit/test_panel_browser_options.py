"""The pytest options of the panel's real-browser checks (PR 11c-2a): --panel and --panel-browser.

tests/browser is not even collected without --panel, so `make test` (and `make check`) never
imports Playwright, which only the `panel` dependency group installs.
"""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import conftest

REPO = Path(__file__).resolve().parents[2]
BROWSER_TESTS = REPO / "tests" / "browser"


def _config(panel: bool, browsers: str = conftest.ALL_BROWSERS) -> pytest.Config:
    options = {"--panel": panel, "--panel-browser": browsers}
    return SimpleNamespace(getoption=options.__getitem__)  # type: ignore[return-value]


def test_the_browser_tests_are_not_collected_without_panel() -> None:
    for path in (BROWSER_TESTS, BROWSER_TESTS / "test_panel_pages.py"):
        assert conftest.pytest_ignore_collect(path, _config(panel=False)) is True


def test_with_panel_the_browser_tests_are_collected() -> None:
    assert conftest.pytest_ignore_collect(BROWSER_TESTS, _config(panel=True)) is None


def test_other_tests_are_never_ignored() -> None:
    other = REPO / "tests" / "unit" / "test_config.py"
    assert conftest.pytest_ignore_collect(other, _config(panel=False)) is None
    browserish = REPO / "tests" / "browsers_not_this" / "test_x.py"
    assert conftest.pytest_ignore_collect(browserish, _config(panel=False)) is None


def test_the_three_engines_by_default() -> None:
    assert conftest.panel_browsers(_config(panel=True)) == ["chromium", "firefox", "webkit"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [("chromium", ["chromium"]), (" webkit , firefox ", ["webkit", "firefox"])],
)
def test_panel_browser_takes_a_comma_list(value: str, expected: list[str]) -> None:
    assert conftest.panel_browsers(_config(panel=True, browsers=value)) == expected


@pytest.mark.parametrize("value", ["", "chrome", "chromium,chromium", "chromium,,webkit"])
def test_an_unknown_empty_or_repeated_browser_is_a_usage_error(value: str) -> None:
    with pytest.raises(pytest.UsageError):
        conftest.panel_browsers(_config(panel=True, browsers=value))


def test_collecting_without_panel_never_imports_playwright() -> None:
    code = (
        "import sys, pytest\n"
        "code = pytest.main(['--collect-only', '-q', '--no-cov', '-p', 'no:cacheprovider',"
        " 'tests/browser'])\n"
        "print(int(code), 'playwright' in sys.modules, 'axe_playwright_python' in sys.modules)\n"
    )
    result = subprocess.run(  # noqa: S603 - this Python, fixed arguments
        [sys.executable, "-c", code],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    # 5 is pytest's NO_TESTS_COLLECTED: the folder was skipped, not broken (a collection error
    # is 2 or 3), and nothing of the panel group was imported.
    assert result.stdout.strip().splitlines()[-1] == "5 False False", result.stdout[-2000:]
    assert "error" not in result.stdout.lower(), result.stdout[-2000:]
