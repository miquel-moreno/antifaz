"""The e2e stack refuses to start unless both providers point at the fake one (tests/e2e/egress.py).

The guard reads the throwaway env file the e2e fixture writes: if a provider base URL could
reach a real provider (missing, another host, https, a user part that hides the real host...),
the session stops before Compose starts.
"""

import pytest

from tests.e2e.egress import UPSTREAM_VARS, read_env, upstream_problems

FAKE_OPENAI = "http://fake-upstream:9000/v1"
FAKE_ANTHROPIC = "http://fake-upstream:9000"


def _env(
    openai: str | None = FAKE_OPENAI, anthropic: str | None = FAKE_ANTHROPIC
) -> dict[str, str]:
    env = {"ANTIFAZ_LOG_LEVEL": "INFO"}
    if openai is not None:
        env["ANTIFAZ_OPENAI_BASE_URL"] = openai
    if anthropic is not None:
        env["ANTIFAZ_ANTHROPIC_BASE_URL"] = anthropic
    return env


def test_both_urls_on_the_fake_provider_pass() -> None:
    assert upstream_problems(_env()) == []


@pytest.mark.parametrize(
    "url",
    [
        "http://fake-upstream:9000",
        "http://fake-upstream:9000/",
        "http://fake-upstream:9000/v1",
        "http://FAKE-UPSTREAM:9000/v1",
    ],
)
def test_any_path_on_the_fake_provider_passes(url: str) -> None:
    assert upstream_problems(_env(openai=url, anthropic=url)) == []


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://api.openai.com/v1",
        "https://api.anthropic.com",
        "http://api.openai.com/v1",
        "https://fake-upstream:9000/v1",  # https: not the fake provider
        "http://fake-upstream/v1",  # port 80
        "http://fake-upstream:443/v1",
        "http://fake-upstream.example.com:9000/v1",
        "http://fake-upstream:9000@api.openai.com/v1",  # the real host hidden behind a user
        "http://user@fake-upstream:9000/v1",
        "http://api.openai.com/fake-upstream:9000",
        "fake-upstream:9000",
        "http://fake-upstream:notaport",
        " http://fake-upstream:9000",
    ],
)
def test_a_url_that_is_not_the_fake_provider_is_refused(url: str) -> None:
    problems = upstream_problems(_env(openai=url))
    assert len(problems) == 1
    assert "ANTIFAZ_OPENAI_BASE_URL" in problems[0]
    if url.strip():
        assert url.strip() not in problems[0]  # the variable is named, never its value


def test_a_missing_url_is_refused_because_the_default_is_the_real_provider() -> None:
    problems = upstream_problems(_env(openai=None, anthropic=None))
    assert len(problems) == 2
    for name, problem in zip(UPSTREAM_VARS, problems, strict=True):
        assert name in problem


def test_each_bad_url_gives_its_own_message() -> None:
    problems = upstream_problems(_env(anthropic="https://api.anthropic.com"))
    assert len(problems) == 1
    assert "ANTIFAZ_ANTHROPIC_BASE_URL" in problems[0]


def test_read_env_reads_name_value_lines() -> None:
    text = (
        "# comment\n\nANTIFAZ_API_KEY=abc\nANTIFAZ_OPENAI_BASE_URL=http://fake-upstream:9000/v1\n"
    )
    assert read_env(text) == {
        "ANTIFAZ_API_KEY": "abc",
        "ANTIFAZ_OPENAI_BASE_URL": "http://fake-upstream:9000/v1",
    }


def test_read_env_keeps_the_last_value_and_an_equals_sign_in_the_value() -> None:
    text = "A=1\nA=2\nB=x=y\nnot a line\n"
    assert read_env(text) == {"A": "2", "B": "x=y"}


def test_the_guard_reads_the_file_the_e2e_fixture_writes() -> None:
    """The real fixture's lines: what conftest.py writes passes the guard."""
    from tests.e2e import conftest

    assert (
        upstream_problems(
            read_env(conftest.env_text({"gateway": "a", "openai": "b", "anthropic": "c"}))
        )
        == []
    )
