"""find_patterns() never breaks its contract, and finds generated cards and mobiles."""

from hypothesis import given, settings
from hypothesis import strategies as st

from antifaz.detect.patterns.personal import find_patterns
from antifaz.detect.types import EntityType, Span
from antifaz.detect.validators import luhn

# Text rich in the characters the patterns are made of, so the candidate search is exercised.
PATTERN_LIKE = st.text(
    alphabet=st.sampled_from(list("0123456789@.-+ /,()ºªnCcalePASBDtelmóvi")),
    max_size=200,
)
# Surrounding text without digits, so it cannot glue itself to the generated value.
WORDS = st.text(alphabet=st.sampled_from(list("abcdefghij xyzñáé,;:")), max_size=40)


def _assert_in_bounds(text: str, spans: list[Span]) -> None:
    assert isinstance(spans, list)
    for span in spans:
        assert isinstance(span, Span)
        assert 0 <= span.start < span.end <= len(text)


@settings(max_examples=1000, deadline=None)
@given(text=st.text())
def test_find_patterns_never_raises_and_returns_in_bounds_spans(text: str) -> None:
    _assert_in_bounds(text, find_patterns(text))


@settings(max_examples=1000, deadline=None)
@given(text=PATTERN_LIKE)
def test_find_patterns_on_pattern_like_text_returns_in_bounds_spans(text: str) -> None:
    _assert_in_bounds(text, find_patterns(text))


# (prefix, allowed lengths): Visa, Mastercard (both ranges), Amex, Discover.
CARD_BRANDS = [
    ("4", (13, 16, 19)),
    *((str(p), (16,)) for p in range(51, 56)),
    ("2221", (16,)),
    ("2500", (16,)),
    ("2720", (16,)),
    ("34", (15,)),
    ("37", (15,)),
    ("6011", (16, 19)),
    ("65", (16, 19)),
    *((str(p), (16,)) for p in range(644, 650)),
]


@st.composite
def card_numbers(draw: st.DrawFn) -> str:
    prefix, lengths = draw(st.sampled_from(CARD_BRANDS))
    length = draw(st.sampled_from(lengths))
    middle = draw(
        st.text(
            alphabet="0123456789",
            min_size=length - len(prefix) - 1,
            max_size=length - len(prefix) - 1,
        )
    )
    body = prefix + middle
    number = body + str(luhn.check_digit(body))
    separator = draw(st.sampled_from(["", " ", "-"]))
    if separator and length == 16:
        return separator.join(number[i : i + 4] for i in range(0, 16, 4))
    return number


@settings(max_examples=500, deadline=None)
@given(before=WORDS, card=card_numbers(), after=WORDS)
def test_a_valid_card_in_random_text_is_found(before: str, card: str, after: str) -> None:
    text = f"{before} {card} {after}"
    start = len(before) + 1
    spans = find_patterns(text)
    assert any(
        s.type is EntityType.CREDIT_CARD and s.start == start and s.end == start + len(card)
        for s in spans
    )


@st.composite
def mobile_numbers(draw: st.DrawFn) -> str:
    digits = "6" + draw(st.text(alphabet="0123456789", min_size=8, max_size=8))
    grouping = draw(st.sampled_from([(9,), (3, 3, 3), (3, 2, 2, 2)]))
    separator = draw(st.sampled_from([" ", ".", "-"]))
    parts, position = [], 0
    for size in grouping:
        parts.append(digits[position : position + size])
        position += size
    return separator.join(parts)


@settings(max_examples=500, deadline=None)
@given(before=WORDS, phone=mobile_numbers(), after=WORDS)
def test_a_spanish_mobile_in_random_text_is_found(before: str, phone: str, after: str) -> None:
    text = f"{before} {phone} {after}"
    start = len(before) + 1
    spans = find_patterns(text)
    assert any(
        s.type is EntityType.PHONE and s.start == start and s.end == start + len(phone)
        for s in spans
    )
