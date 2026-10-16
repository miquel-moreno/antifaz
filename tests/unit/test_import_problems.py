"""How `make e2e` judges the import of every module in the image (issue 54, review of PR B2).

tests/e2e/test_image_imports.py imports every module of every installed package inside the
image and hands the output to `import_problems`. A standard-library module was removed only
because nothing needs it, so the judgement is strict: any failure inside `antifaz` or of a
top-level name fails; an optional package may be missing only if it is on the explicit list
(with its reason), and a listed package that is no longer missing fails too, so the list stays
true. Synthetic outputs with the probe's line format.
"""

import pytest

from tests.e2e.image_probes import EXPECTED_MISSING, import_problems

EXPECTED = {"trio": "anyio's optional backend", "h2": "HTTP/2 for httpcore"}
OK = "\n".join(
    [
        "modules 950",
        "fail anyio._backends._trio not-installed trio ",
        "fail httpcore._sync.http2 not-installed h2 ",
        "fail httpcore._async.http2 not-installed h2.connection ",
        "fail click._winconsole stdlib _ctypes ",
    ]
)


def _problems(output: str) -> list[str]:
    return import_problems(output, EXPECTED, windows_only=frozenset({"click._winconsole"}))


def test_only_the_listed_optional_packages_and_windows_modules_may_fail() -> None:
    assert _problems(OK) == []


@pytest.mark.parametrize(
    "line",
    [
        "fail antifaz.detect.ner.gliner not-installed torch ",
        "fail antifaz other ValueError ",
        "fail antifaz.web.assets broken antifaz ",
    ],
)
def test_any_failure_inside_antifaz_fails(line: str) -> None:
    problems = _problems(f"{OK}\n{line}")
    assert any(line.split()[1] in problem for problem in problems), problems


def test_a_top_level_name_that_does_not_import_fails() -> None:
    assert _problems(f"{OK}\nfail yaml not-installed trio top")


@pytest.mark.parametrize(
    "line",
    [
        "fail uvicorn.workers not-installed gunicorn ",  # not on the list
        "fail somepkg.mod stdlib _sqlite3 ",
        "fail somepkg.mod broken pydantic_core ",
        "fail somepkg.mod other RuntimeError ",
    ],
)
def test_an_unexpected_failure_fails(line: str) -> None:
    problems = _problems(f"{OK}\n{line}")
    assert any("somepkg.mod" in p or "gunicorn" in p for p in problems), problems


def test_a_listed_package_that_is_no_longer_missing_fails() -> None:
    output = "\n".join(line for line in OK.splitlines() if "h2" not in line)
    assert _problems(output) == ["listed as missing but nothing misses it now: h2"]


def test_too_few_modules_fails() -> None:
    assert _problems(OK.replace("modules 950", "modules 899"))


def test_every_expected_missing_package_has_a_reason() -> None:
    assert EXPECTED_MISSING
    for package, reason in EXPECTED_MISSING.items():
        assert package.isidentifier() and len(reason) > 10, package
