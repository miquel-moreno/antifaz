"""Benchmark metrics keep their invariants for any text and any set of annotations."""

import random

from evals.metrics import Annotation, leaks, overlap_counts, strict_counts
from hypothesis import given, settings
from hypothesis import strategies as st

LABELS = ["L1", "L2", "L3"]


@st.composite
def text_and_annotations(draw: st.DrawFn) -> tuple[str, list[Annotation], list[Annotation]]:
    """A text plus gold and predicted non-empty annotations inside it."""
    text = draw(st.text(alphabet=st.sampled_from(list("ab1 .-é\r\n")), min_size=1, max_size=40))

    def annotation() -> st.SearchStrategy[Annotation]:
        return (
            st.tuples(
                st.integers(0, len(text) - 1),
                st.integers(1, len(text)),
                st.sampled_from(LABELS),
            )
            .filter(lambda t: t[0] < t[1])
            .map(lambda t: Annotation(t[0], t[1], t[2]))
        )

    gold = draw(st.lists(annotation(), max_size=8))
    predicted = draw(st.lists(annotation(), max_size=8))
    return text, gold, predicted


Case = tuple[str, list[Annotation], list[Annotation]]


@settings(max_examples=300, deadline=None)
@given(case=text_and_annotations())
def test_predicting_exactly_the_gold_gives_full_recall_and_no_leaks(case: Case) -> None:
    text, gold, _ = case
    for label, counts in overlap_counts(gold, list(gold)).items():
        assert counts.fn == 0, label
        assert counts.fp == 0, label
        assert counts.recall == 1.0, label
    for label, (leaked, _total) in leaks(text, gold, list(gold)).items():
        assert leaked == 0, label


@settings(max_examples=300, deadline=None)
@given(case=text_and_annotations())
def test_counts_add_up_to_the_number_of_gold_and_predicted(case: Case) -> None:
    _, gold, predicted = case
    for counting in (overlap_counts, strict_counts):
        result = counting(gold, predicted)
        for label in LABELS:
            counts = result.get(label)
            n_gold = sum(1 for a in gold if a.label == label)
            n_pred = sum(1 for a in predicted if a.label == label)
            if counts is None:
                assert n_gold == n_pred == 0
                continue
            assert counts.tp + counts.fn == n_gold
            assert counts.tp + counts.fp == n_pred


@settings(max_examples=300, deadline=None)
@given(case=text_and_annotations())
def test_leak_totals_count_every_gold_value(case: Case) -> None:
    text, gold, predicted = case
    result = leaks(text, gold, predicted)
    assert set(result) == {a.label for a in gold}
    for label, (leaked, total) in result.items():
        assert total == sum(1 for a in gold if a.label == label)
        assert 0 <= leaked <= total


@settings(max_examples=300, deadline=None)
@given(case=text_and_annotations())
def test_removing_a_prediction_never_decreases_leaks(case: Case) -> None:
    text, gold, predicted = case
    before = leaks(text, gold, predicted)
    for i in range(len(predicted)):
        after = leaks(text, gold, predicted[:i] + predicted[i + 1 :])
        for label, (leaked, total) in before.items():
            assert after[label][0] >= leaked
            assert after[label][1] == total


@settings(max_examples=300, deadline=None)
@given(case=text_and_annotations(), seed=st.integers(0, 2**32 - 1))
def test_metrics_do_not_depend_on_the_order_of_the_inputs(case: Case, seed: int) -> None:
    text, gold, predicted = case
    rng = random.Random(seed)  # noqa: S311 - shuffling test data, not security
    gold_shuffled = rng.sample(gold, len(gold))
    predicted_shuffled = rng.sample(predicted, len(predicted))
    assert overlap_counts(gold_shuffled, predicted_shuffled) == overlap_counts(gold, predicted)
    assert strict_counts(gold_shuffled, predicted_shuffled) == strict_counts(gold, predicted)
    assert leaks(text, gold_shuffled, predicted_shuffled) == leaks(text, gold, predicted)
