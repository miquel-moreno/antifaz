"""Invariants 1, 5 and 6 of the spec, with Hypothesis.

1. restore(mask(x)) == x for any text, including text that already contains [[...]].
5. The same value gets the same placeholder in the whole conversation.
6. Only placeholders emitted in this request are restored.
"""

import re
from collections.abc import Sequence

from hypothesis import given, settings
from hypothesis import strategies as st

from antifaz import mask, restore
from antifaz.detect.types import Confidence, EntityType, Layer, Span

# Synthetic values with valid check digits: never real data.
VALUES = ["12345678Z", "87654321X", "00000000T", "ana@example.com", "ES9121000418450200051332"]
FAKE_PLACEHOLDERS = ["[[ES_DNI_1]]", "[[ es_dni_2 ]]", "[[EMAIL_1]]", "[[!", "[[[", "]]"]

adversarial = st.text(alphabet=st.sampled_from(list("[]! _abcXYZ019-.")), max_size=30)
pieces = st.one_of(adversarial, st.sampled_from(VALUES), st.sampled_from(FAKE_PLACEHOLDERS))
texts = st.lists(pieces, max_size=12).map(" ".join) | st.lists(pieces, max_size=12).map("".join)
any_text = texts | st.text(max_size=80)

PLACEHOLDER = re.compile(r"\[\[([A-Z]+(?:_[A-Z]+)*)_([0-9]+)\]\]")
UNESCAPE = re.compile(r"(\[\[+)!")


@settings(max_examples=500, deadline=None)
@given(text=any_text)
def test_restore_of_mask_is_identity_with_the_real_detector(text: str) -> None:
    result = mask(text)
    assert restore(result.text, result.vault) == text


@st.composite
def text_and_spans(draw: st.DrawFn) -> tuple[str, list[Span]]:
    text = draw(any_text)
    cuts = sorted(draw(st.sets(st.integers(0, len(text)), max_size=10)))
    spans = []
    for start, end in zip(cuts[::2], cuts[1::2], strict=False):
        if start < end:
            spans.append(
                Span(start, end, draw(st.sampled_from(EntityType)), Layer.PATTERN, Confidence.HIGH)
            )
    return text, spans


@settings(max_examples=500, deadline=None)
@given(case=text_and_spans())
def test_restore_of_mask_is_identity_with_arbitrary_spans(case: tuple[str, list[Span]]) -> None:
    text, spans = case

    def detector(_: str) -> Sequence[Span]:
        return spans

    result = mask(text, detector=detector)
    assert restore(result.text, result.vault) == text


@settings(max_examples=300, deadline=None)
@given(conversation=st.lists(texts, min_size=1, max_size=5))
def test_same_value_same_placeholder_and_numbering_without_gaps(conversation: list[str]) -> None:
    result = mask(conversation)
    seen: dict[str, set[int]] = {}
    token_to_value: dict[str, str] = {}
    for masked, original in zip(result.texts, conversation, strict=True):
        assert restore(masked, result.vault) == original
        for match in PLACEHOLDER.finditer(masked):
            token = f"{match.group(1)}_{match.group(2)}"
            value = restore(match.group(0), result.vault)
            if value == match.group(0):
                continue  # an escaped fake placeholder from the user, not ours
            assert token_to_value.setdefault(token, value) == value
            seen.setdefault(match.group(1), set()).add(int(match.group(2)))
    # One token per value, and numbers 1..n per type.
    assert len(set(token_to_value.values())) == len(token_to_value)
    for numbers in seen.values():
        assert numbers == set(range(1, len(numbers) + 1))


@settings(max_examples=300, deadline=None)
@given(text=texts, answer=texts)
def test_placeholders_of_another_request_are_left_unchanged(text: str, answer: str) -> None:
    other = mask(text)
    empty = mask("no personal data here")
    # With an empty table, restore only undoes escapes: every placeholder stays verbatim.
    for candidate in (answer, other.text):
        assert restore(candidate, empty.vault) == UNESCAPE.sub(r"\1", candidate)
