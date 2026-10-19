"""Properties of «Prueba un texto» (issue 53, PR 11d-a): web/try_text.py.

1. UTF-16 offsets: for any text around the synthetic values (emoji, combining marks, CRLF, RTL,
   any code point but lone surrogates), every entity's UTF-16 range sliced from the original
   text as JavaScript would (UTF-16-LE code units) is exactly the code-point slice mask() used,
   and length_utf16 is the text's length in UTF-16 units.
2. simulate_reply(): whatever tokens it gets, removing the markers from the reply leaves the
   same text for the same number of tokens and language: nothing of a token but the marker
   itself, and nothing else at all, can reach the reply.
3. The joined segments: sent == the masked text the provider would receive (hidden values never
   in it), reply == restore() of the whole simulated reply.
"""

import re

from hypothesis import given, settings
from hypothesis import strategies as st

from antifaz import mask
from antifaz.detect.scan import scan
from antifaz.policy import DEFAULT_POLICY
from antifaz.web.try_text import MAX_LISTED, run_try, simulate_reply, utf16_offsets
from tests.conftest import SENTINEL_DNI

VALUES = [SENTINEL_DNI, "ana@example.com", "ES9121000418450200051332", "612345678"]
SPECIAL = [
    chr(0x301),  # combining acute
    chr(0x200D),  # zero width joiner
    chr(0x202E),  # right-to-left override
    chr(0x1F44D),  # astral emoji
    chr(0x1F1EA),  # regional indicator
    "\r\n",
    "ש",  # Hebrew
    "م",  # Arabic
    " ",
    "é",
]
NOISE = st.lists(
    st.sampled_from(SPECIAL) | st.characters(blacklist_categories=("Cs",)), max_size=12
).map("".join)
TOKENS = st.lists(st.from_regex(r"\A[A-Z]{1,6}(?:_[A-Z]{1,4}){0,2}_[1-9][0-9]{0,2}\Z"), max_size=20)
MARKER = re.compile(r"\[\[[A-Z]+(?:_[A-Z]+)*_[0-9]+\]\]")


def _js_slice(text: str, start: int, end: int) -> str:
    return text.encode("utf-16-le")[2 * start : 2 * end].decode("utf-16-le")


@settings(max_examples=150, deadline=None)
@given(before=NOISE, after=NOISE, value=st.sampled_from(VALUES), between=NOISE)
def test_the_utf16_slice_of_every_entity_is_its_code_point_slice(
    before: str, after: str, value: str, between: str
) -> None:
    text = f"{before} {value} {between}{value}{after}"

    result = run_try(text, "en", detector=scan, policy=DEFAULT_POLICY)
    spans = mask(text).found_spans[0]

    assert result["length_utf16"] == len(text.encode("utf-16-le")) // 2
    assert len(result["entities"]) == len(spans)
    for entity, span in zip(result["entities"], spans, strict=True):
        assert _js_slice(text, entity["start"], entity["end"]) == text[span.start : span.end]
    slices = [_js_slice(text, e["start"], e["end"]) for e in result["entities"]]
    assert any(value in found for found in slices)


@given(st.text(max_size=40).filter(lambda t: all(not 0xD800 <= ord(c) <= 0xDFFF for c in t)))
def test_offsets_are_monotonic_and_end_at_the_utf16_length(text: str) -> None:
    offsets = utf16_offsets(text, range(len(text) + 1))

    values = [offsets[i] for i in range(len(text) + 1)]
    assert values == sorted(values)
    assert values[-1] == len(text.encode("utf-16-le")) // 2
    for i in range(len(text)):
        assert _js_slice(text, offsets[i], offsets[i + 1]) == text[i]


@given(TOKENS, TOKENS, st.sampled_from(["en", "es"]))
def test_the_reply_carries_nothing_of_the_tokens_but_the_markers(
    first: list[str], second: list[str], lang: str
) -> None:
    first = list(dict.fromkeys(first))
    second = list(dict.fromkeys(second))
    a, b = simulate_reply(first, lang), simulate_reply(second, lang)

    assert MARKER.findall(a) == [f"[[{t}]]" for t in first[:MAX_LISTED]]
    if len(first) == len(second):
        assert MARKER.sub("", a) == MARKER.sub("", b)


@settings(max_examples=60, deadline=None)
@given(before=NOISE, after=NOISE, value=st.sampled_from(VALUES))
def test_joined_segments_are_the_masked_text_and_the_restored_reply(
    before: str, after: str, value: str
) -> None:
    text = f"{before} {value} {after}"

    result = run_try(text, "es", detector=scan, policy=DEFAULT_POLICY)

    if result["outcome"] == "blocked":  # the guard is allowed to be stricter than the masker
        assert result["sent"] == [] and result["reply"] == []
        return
    sent = "".join(s.get("text", s.get("marker", "")) for s in result["sent"])
    assert sent == mask(text).text
    assert value not in sent
    reply_markers = "".join(s.get("text", s.get("marker", "")) for s in result["reply"])
    tokens = [s["marker"][2:-2] for s in result["sent"] if "marker" in s]
    assert reply_markers == simulate_reply(list(dict.fromkeys(tokens)), "es")
    restored = "".join(s.get("text", s.get("value", "")) for s in result["reply"])
    assert value in restored
