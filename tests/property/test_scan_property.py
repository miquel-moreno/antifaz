"""scan() and resolve() never break their output contract, whatever the input."""

from itertools import pairwise

from hypothesis import given, settings
from hypothesis import strategies as st

from antifaz.detect.overlaps import resolve
from antifaz.detect.scan import scan
from antifaz.detect.types import Confidence, EntityType, Layer, Span

# Text rich in the characters identifiers are made of, so the candidate search is exercised.
IDENTIFIER_LIKE = st.text(
    alphabet=st.sampled_from(list("0123456789XYZKLMPBESDE -./,")),
    max_size=200,
)


def _assert_well_formed(text: str, spans: list[Span]) -> None:
    assert isinstance(spans, list)
    for span in spans:
        assert isinstance(span, Span)
        assert 0 <= span.start < span.end <= len(text)
    for left, right in pairwise(spans):
        assert left.end <= right.start  # sorted and non-overlapping


@settings(max_examples=1000, deadline=None)
@given(text=st.text())
def test_scan_never_raises_and_returns_sorted_non_overlapping_spans(text: str) -> None:
    _assert_well_formed(text, scan(text))


@settings(max_examples=1000, deadline=None)
@given(text=IDENTIFIER_LIKE)
def test_scan_of_identifier_like_text_returns_sorted_non_overlapping_spans(text: str) -> None:
    _assert_well_formed(text, scan(text))


spans_strategy = st.lists(
    st.builds(
        lambda start, length, type_, layer, confidence: Span(
            start, start + length, type_, layer, confidence
        ),
        st.integers(0, 100),
        st.integers(1, 30),
        st.sampled_from(EntityType),
        st.sampled_from(Layer),
        st.sampled_from(Confidence),
    ),
    max_size=20,
)


@settings(max_examples=1000, deadline=None)
@given(spans=spans_strategy)
def test_resolve_keeps_a_sorted_non_overlapping_subset(spans: list[Span]) -> None:
    result = resolve(spans)
    assert set(result) <= set(spans)
    for left, right in pairwise(result):
        assert left.end <= right.start


@settings(max_examples=500, deadline=None)
@given(spans=spans_strategy, data=st.data())
def test_resolve_does_not_depend_on_input_order(spans: list[Span], data: st.DataObject) -> None:
    shuffled = data.draw(st.permutations(spans))
    assert resolve(shuffled) == resolve(spans)


@settings(max_examples=500, deadline=None)
@given(spans=spans_strategy)
def test_every_discarded_span_overlaps_a_kept_span(spans: list[Span]) -> None:
    result = resolve(spans)
    for span in set(spans) - set(result):
        assert any(span.start < kept.end and kept.start < span.end for kept in result)
