"""Recognisable pieces of a configured key (invariant 13, issue 76).

A provider that rejects its key may quote part of it: the generic prefix and the last four
characters behind a run of `*` or dots (`sk-proj-****...WxYz`). KeyFragments finds those shapes
and any run of FRAGMENT_LENGTH consecutive characters of a key's secret part (the key without
its generic, public prefix such as `sk-`, `sk-proj-` or `sk-ant-api03-`).
"""

import json
import random
import string
import time
from collections.abc import Sequence

import httpx
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from antifaz.api import proxy
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


# --- More masked shapes (review of issue 76) ---------------------------------------------------


@pytest.mark.parametrize("visible", range(4, FRAGMENT_LENGTH))
@pytest.mark.parametrize("mask", ["**", "********", "...", "…", "••••"])
def test_a_mask_followed_by_the_last_four_to_nine_characters_is_found(
    visible: int, mask: str
) -> None:
    shape = "sk-proj-" + mask + secret_part(OPENAI)[-visible:]
    assert KeyFragments(KEYS).found_in(f"key {shape}. Check it")
    assert KeyFragments(KEYS).found_in(shape)


@pytest.mark.parametrize("visible", range(4, FRAGMENT_LENGTH))
@pytest.mark.parametrize("mask", ["**", "********", "...", "…"])
def test_the_first_four_to_nine_characters_followed_by_a_mask_are_found(
    visible: int, mask: str
) -> None:
    secret = secret_part(OPENAI)
    assert KeyFragments(KEYS).found_in(f"key sk-proj-{secret[:visible]}{mask} is wrong")
    assert KeyFragments(KEYS).found_in(f"key sk-proj-{secret[:visible]}{mask}{OPENAI[-2:]}")
    assert KeyFragments(KEYS).found_in(f"({secret[:visible]}{mask})")


@pytest.mark.parametrize(
    "text",
    [
        "**" + OPENAI[-SHOWN_SUFFIX:] + "**",  # markdown bold, not a masked key
        "**" + secret_part(OPENAI)[:SHOWN_SUFFIX] + "**",
        "**" + secret_part(OPENAI)[-6:] + "**",
        secret_part(OPENAI)[:SHOWN_SUFFIX] + "*",  # one star after the first four
        "x" + secret_part(OPENAI)[:6] + "***",  # the first characters inside another word
        secret_part(OPENAI)[:3] + "***",  # three are too few
        "**" + OPENAI[-3:],
    ],
)
def test_more_shapes_that_are_not_fragments(text: str) -> None:
    assert not KeyFragments(KEYS).found_in(text)


# --- The fast path is the slow, obvious rule (review of issue 76) -------------------------------

MASK = "*•·.…"


def _masked_before(text: str, position: int) -> bool:
    if position >= 1 and text[position - 1] == "…":
        return True
    return position >= 2 and text[position - 1] in MASK and text[position - 2] in MASK


def _masked_after(text: str, end: int) -> bool:
    if end < len(text) and text[end] == "…":
        return True
    return end + 1 < len(text) and text[end] in MASK and text[end + 1] in MASK


def _free_after(text: str, end: int) -> bool:
    return end >= len(text) or not (text[end].isalnum() or text[end] in "*•")


def _free_before(text: str, position: int) -> bool:
    return position == 0 or not (text[position - 1].isalnum() or text[position - 1] in MASK)


def _occurrences(text: str, piece: str) -> list[int]:
    return [i for i in range(len(text) - len(piece) + 1) if text.startswith(piece, i)]


def reference(keys: tuple[str, ...], text: str) -> bool:
    """The rules written the slow, obvious way: every run, every shape, one by one."""
    for key in keys:
        if not key:
            continue
        secret = secret_part(key)
        if key in text:
            return True
        runs = (secret[i : i + FRAGMENT_LENGTH] for i in range(len(secret) - FRAGMENT_LENGTH + 1))
        if any(run in text for run in runs):
            return True
        if len(secret) < 2 * SHOWN_SUFFIX:
            continue
        for visible in range(SHOWN_SUFFIX, min(FRAGMENT_LENGTH, len(secret))):
            tail, head = secret[-visible:], secret[:visible]
            if any(
                _masked_before(text, p) and _free_after(text, p + visible)
                for p in _occurrences(text, tail)
            ):
                return True
            if any(
                _free_before(text, p) and _masked_after(text, p + visible)
                for p in _occurrences(text, head)
            ):
                return True
    return False


# Characters of the canaries plus hexadecimal, so random text often touches a key.
FILLER = "".join(sorted(set("".join(KEYS) + "0123456789abcdef")))


@st.composite
def planted_texts(draw: st.DrawFn) -> tuple[tuple[str, ...], str]:
    hexkey = draw(st.text("0123456789abcdef", min_size=32, max_size=64))
    keys = (*KEYS, hexkey)
    key = draw(st.sampled_from(keys))
    secret = secret_part(key)
    filler = st.text(FILLER + MASK + " .\n", max_size=60)
    start = draw(st.integers(0, max(0, len(secret) - 1)))
    size = draw(st.integers(1, 14))
    piece = secret[start : start + size]
    mask = draw(st.sampled_from(["", "*", "**", "...", "…", "•••"]))
    planted = draw(st.sampled_from([mask + piece, piece + mask, piece]))
    return keys, draw(filler) + planted + draw(filler)


@settings(max_examples=600, deadline=None)
@given(planted_texts())
def test_the_fast_search_agrees_with_the_obvious_rule(case: tuple[tuple[str, ...], str]) -> None:
    keys, text = case
    assert KeyFragments(keys).found_in(text) == reference(keys, text)


@settings(max_examples=300, deadline=None)
@given(st.text(FILLER + MASK + " ", max_size=200))
def test_the_fast_search_agrees_on_random_text(text: str) -> None:
    assert KeyFragments(KEYS).found_in(text) == reference(KEYS, text)


# --- Cost (review of issue 76): relative to the whole-key check, never a wall-clock limit -------


def _stream_keys() -> list[str]:
    rnd = random.Random(76)  # noqa: S311 - fixed seed for invented test keys, not security
    base62 = string.ascii_letters + string.digits
    return [
        "sk-proj-" + "".join(rnd.choice(base62) for _ in range(156)),
        "sk-ant-api03-" + "".join(rnd.choice(base62 + "-_") for _ in range(95)),
        "".join(rnd.choice("0123456789abcdef") for _ in range(64)),
        "".join(rnd.choice("0123456789abcdef") for _ in range(64)),
    ]


def _stream_events() -> list[str]:
    rnd = random.Random(7)  # noqa: S311 - fixed seed for test data, not security
    words = [
        "el",
        "la",
        "de",
        "que",
        "y",
        "en",
        "un",
        "ser",
        "se",
        "no",
        "haber",
        "por",
        "con",
        "su",
        "para",
        "como",
        "estar",
        "tener",
    ]
    return [_delta(" " + rnd.choice(words)) for _ in range(2000)]


def _cpu(watch: KeyWatch, events: list[str]) -> float:
    parsed = [SSEParser().feed(raw)[0] for raw in events]
    start = time.process_time()
    for raw, event in zip(events, parsed, strict=True):
        watch.check(event)
        watch.check_output(raw)
    return time.process_time() - start


class _WholeKeyWatch(KeyWatch):
    """KeyWatch as it was before issue 76: only the whole keys."""

    def _holds(self, text: str) -> bool:
        return any(key in text for key in self._keys)


def test_watching_fragments_costs_at_most_a_few_times_the_whole_key_check() -> None:
    """A 2,000-event answer with four realistic keys: CPU time relative to the old check.

    Before the fast path it was about 12 times; the bound leaves room for noise."""
    keys, events = _stream_keys(), _stream_events()
    fragments = min(_cpu(KeyWatch(keys), events) for _ in range(5))
    whole = min(_cpu(_WholeKeyWatch(keys), events) for _ in range(5))
    assert fragments <= 4 * max(whole, 0.016), (fragments, whole)  # 0.016: Windows' clock tick


def test_an_answer_body_builds_its_fragments_once(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[int] = []

    class Counting(KeyFragments):
        def __init__(self, keys: Sequence[str]) -> None:
            built.append(1)
            super().__init__(keys)

    monkeypatch.setattr(proxy, "KeyFragments", Counting)
    upstream = httpx.Response(400, json={"error": {"message": "nothing here"}})
    assert not proxy._echoes_a_key(upstream, [OPENAI, ANTHROPIC])
    assert len(built) == 1
