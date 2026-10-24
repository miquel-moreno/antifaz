"""Recognisable pieces of a configured key (invariant 13, issue 76).

A provider that rejects its key may quote part of it: the generic prefix and the last four
characters behind a run of `*` or dots (`sk-proj-****...WxYz`). KeyFragments finds those shapes
and any run of FRAGMENT_LENGTH consecutive characters of a key's secret part (the key without
its generic, public prefix such as `sk-`, `sk-proj-` or `sk-ant-api03-`).
"""

import json

import pytest

from antifaz.api.key_fragments import FRAGMENT_LENGTH, SHOWN_SUFFIX, KeyFragments, secret_part
from antifaz.api.proxy import contains_key
from antifaz.api.streaming import KeyEchoed, KeyWatch
from antifaz.providers.sse import SSEParser

# Invented canaries, never real keys (letters outside hexadecimal, no provider checksum).
OPENAI = "sk-proj-QzCanaryOpnWvXkMtRpLsYjGhNqZuVoPiYkGxRw"
ANTHROPIC = "sk-ant-api03-QzCanaryAntWvXkMtRpLsYjGhNqZuVoPj-Qw"
LEGACY = "sk-QzCanaryLegacyWvXkMtRpLsYjGhNq"
GATEWAY = "QzGateCanaryVxJmPtRnLsYhGfDcBaWqUoIeKy"
KEYS = (OPENAI, ANTHROPIC, LEGACY, GATEWAY)


def test_fragment_length_and_shown_suffix_are_the_documented_values() -> None:
    assert FRAGMENT_LENGTH == 10
    assert SHOWN_SUFFIX == 4


@pytest.mark.parametrize(
    ("key", "secret"),
    [
        (OPENAI, OPENAI.removeprefix("sk-proj-")),
        (ANTHROPIC, ANTHROPIC.removeprefix("sk-ant-api03-")),
        (LEGACY, LEGACY.removeprefix("sk-")),
        ("sk-svcacct-QzCanarySvcWvXkMtRpLs", "QzCanarySvcWvXkMtRpLs"),
        ("sk-admin-QzCanaryAdmWvXkMtRpLs", "QzCanaryAdmWvXkMtRpLs"),
        ("sk-ant-admin01-QzCanaryAdmWvXkMtRp", "QzCanaryAdmWvXkMtRp"),
        (GATEWAY, GATEWAY),
    ],
)
def test_secret_part_drops_only_the_generic_public_prefix(key: str, secret: str) -> None:
    assert secret_part(key) == secret


@pytest.mark.parametrize(
    "shape",
    [
        "sk-proj-" + "*" * 20 + OPENAI[-4:],  # OpenAI's "Incorrect API key provided" shape
        "sk-proj-****..." + OPENAI[-4:],
        "sk-proj-****…" + OPENAI[-4:],
        "..." + OPENAI[-4:],
        "…" + OPENAI[-4:],
        "**" + OPENAI[-4:],
        "sk-proj-QzCa" + "*" * 10 + OPENAI[-4:],
        "sk-ant-api03-" + "•" * 8 + ANTHROPIC[-4:],
        "sk-****" + LEGACY[-4:],
    ],
)
def test_provider_style_masked_fragments_are_found(shape: str) -> None:
    fragments = KeyFragments(KEYS)
    assert fragments.found_in(f"Incorrect API key provided: {shape}. You can find your key")
    assert fragments.found_in(shape)


@pytest.mark.parametrize("key", KEYS)
def test_every_run_of_fragment_length_characters_of_the_secret_is_found(key: str) -> None:
    fragments = KeyFragments([key])
    secret = secret_part(key)
    for start in range(len(secret) - FRAGMENT_LENGTH + 1):
        run = secret[start : start + FRAGMENT_LENGTH]
        assert fragments.found_in(f"error: {run} was rejected"), run


@pytest.mark.parametrize("key", KEYS)
def test_a_longer_run_and_the_whole_key_are_found(key: str) -> None:
    fragments = KeyFragments([key])
    assert fragments.found_in(key)
    assert fragments.found_in("x" + secret_part(key)[2:-2] + "y")


@pytest.mark.parametrize(
    "text",
    [
        "sk-",
        "sk-proj-",
        "sk-ant-api03-",
        "use a key that starts with sk-proj- or sk-ant-api03-",
        OPENAI[-4:],  # the last four alone: too short to recognise
        "key ends in " + OPENAI[-4:],
        "..." + OPENAI[-4:] + "z",  # the last four followed by more key-like characters
        "*" + OPENAI[-4:],  # one star is markdown, not a masked key
        "Incorrect API key provided.",
        '{"error": {"type": "authentication_error", "message": "invalid x-api-key"}}',
    ],
)
def test_generic_prefixes_and_short_pieces_are_not_fragments(text: str) -> None:
    assert not KeyFragments(KEYS).found_in(text)


@pytest.mark.parametrize("key", KEYS)
def test_a_run_one_shorter_than_fragment_length_is_not_a_fragment(key: str) -> None:
    fragments = KeyFragments([key])
    secret = secret_part(key)
    assert not fragments.found_in(" " + secret[1:FRAGMENT_LENGTH] + " ")


def test_a_short_secret_is_only_matched_whole() -> None:
    """A key whose secret part is shorter than FRAGMENT_LENGTH: no windows, the whole key still."""
    fragments = KeyFragments(["sk-QzShort"])
    assert fragments.found_in("sk-QzShort")
    assert not fragments.found_in("QzShort")


def test_no_keys_find_nothing() -> None:
    assert not KeyFragments([]).found_in("sk-proj-****...abcd " + OPENAI)
    assert not KeyFragments([""]).found_in("anything")


def test_a_long_run_of_dots_or_stars_is_linear_not_catastrophic() -> None:
    """No regular expression with nested repetition: a megabyte of dots answers at once."""
    fragments = KeyFragments(KEYS)
    assert not fragments.found_in("." * 1_000_000 + "zzzz")
    assert not fragments.found_in("*" * 1_000_000)


# --- KeyWatch and contains_key see fragments too ---------------------------------------------


def _delta(text: str) -> str:
    choice = {"index": 0, "delta": {"content": text}}
    chunk = {"object": "chat.completion.chunk", "choices": [choice]}
    return f"data: {json.dumps(chunk)}\n\n"


def test_keywatch_catches_a_masked_fragment_in_one_event() -> None:
    watch = KeyWatch([OPENAI])
    with pytest.raises(KeyEchoed):
        watch.check_output(_delta("your key sk-proj-****..." + OPENAI[-4:] + " is invalid"))


def test_keywatch_catches_a_run_split_between_two_deltas() -> None:
    watch = KeyWatch([OPENAI])
    run = secret_part(OPENAI)[3 : 3 + FRAGMENT_LENGTH]
    watch.check_output(_delta("a " + run[:5]))
    with pytest.raises(KeyEchoed):
        watch.check_output(_delta(run[5:] + " b"))


def test_keywatch_catches_a_fragment_in_what_the_provider_sent() -> None:
    watch = KeyWatch([ANTHROPIC])
    payload = {"type": "error", "error": {"message": "key ..." + ANTHROPIC[-4:]}}
    event = SSEParser().feed(f"event: error\ndata: {json.dumps(payload)}\n\n")[0]
    with pytest.raises(KeyEchoed):
        watch.check(event)


def test_keywatch_ordinary_text_still_passes() -> None:
    watch = KeyWatch(KEYS)
    for piece in ("Hola, ", "sk-proj- es un prefijo ", "... y nada más", " fin"):
        watch.check_output(_delta(piece))


def test_contains_key_sees_a_fragment_inside_escaped_json() -> None:
    escaped = "".join(f"\\u{ord(c):04x}" for c in secret_part(OPENAI)[:FRAGMENT_LENGTH])
    raw = '{"error": {"message": "bad key ' + escaped + '"}}'
    assert contains_key(raw, [OPENAI])
    assert not contains_key('{"error": {"message": "bad key"}}', [OPENAI])
