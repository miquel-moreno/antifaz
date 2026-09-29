"""Benchmark metrics: overlap vs strict matching, leaks and latency. Labels are invented."""

import pytest
from evals.metrics import (
    Annotation,
    Counts,
    latency_ms,
    leaks,
    leaks_per_100,
    overlap_counts,
    strict_counts,
)

A = Annotation


# --- Counts: precision, recall and F1 ---------------------------------------------------


def test_counts_compute_precision_recall_and_f1() -> None:
    counts = Counts(tp=3, fp=1, fn=2)
    assert counts.precision == pytest.approx(0.75)
    assert counts.recall == pytest.approx(0.6)
    assert counts.f1 == pytest.approx(2 * 0.75 * 0.6 / (0.75 + 0.6))


def test_precision_is_zero_when_there_are_no_predictions() -> None:
    assert Counts(tp=0, fp=0, fn=4).precision == 0.0


def test_recall_is_zero_when_there_is_no_gold() -> None:
    assert Counts(tp=0, fp=3, fn=0).recall == 0.0


def test_f1_is_zero_when_precision_and_recall_are_zero() -> None:
    assert Counts().f1 == 0.0
    assert Counts(tp=0, fp=2, fn=2).f1 == 0.0


def test_perfect_counts_give_one_everywhere() -> None:
    counts = Counts(tp=5)
    assert (counts.precision, counts.recall, counts.f1) == (1.0, 1.0, 1.0)


# --- overlap_counts and strict_counts --------------------------------------------------


def test_exact_match_is_a_true_positive_in_both_modes() -> None:
    gold = [A(0, 5, "EMAIL")]
    predicted = [A(0, 5, "EMAIL")]
    assert overlap_counts(gold, predicted) == {"EMAIL": Counts(tp=1)}
    assert strict_counts(gold, predicted) == {"EMAIL": Counts(tp=1)}


def test_partial_overlap_counts_only_in_overlap_mode() -> None:
    gold = [A(10, 20, "PHONE")]
    predicted = [A(15, 25, "PHONE")]
    assert overlap_counts(gold, predicted) == {"PHONE": Counts(tp=1)}
    assert strict_counts(gold, predicted) == {"PHONE": Counts(fp=1, fn=1)}


def test_a_single_shared_character_is_enough_for_overlap() -> None:
    gold = [A(10, 20, "PHONE")]
    assert overlap_counts(gold, [A(19, 30, "PHONE")]) == {"PHONE": Counts(tp=1)}


def test_touching_ranges_do_not_overlap() -> None:
    gold = [A(10, 20, "PHONE")]
    assert overlap_counts(gold, [A(20, 30, "PHONE")]) == {"PHONE": Counts(fp=1, fn=1)}
    assert overlap_counts(gold, [A(0, 10, "PHONE")]) == {"PHONE": Counts(fp=1, fn=1)}


def test_two_predictions_over_one_gold_give_one_tp_and_one_fp() -> None:
    gold = [A(0, 10, "ADDRESS")]
    predicted = [A(0, 4, "ADDRESS"), A(5, 10, "ADDRESS")]
    assert overlap_counts(gold, predicted) == {"ADDRESS": Counts(tp=1, fp=1)}


def test_one_prediction_over_two_gold_gives_one_tp_and_one_fn() -> None:
    gold = [A(0, 4, "ADDRESS"), A(5, 10, "ADDRESS")]
    predicted = [A(0, 10, "ADDRESS")]
    assert overlap_counts(gold, predicted) == {"ADDRESS": Counts(tp=1, fn=1)}


def test_a_prediction_with_another_label_never_matches() -> None:
    gold = [A(0, 10, "EMAIL")]
    predicted = [A(0, 10, "PHONE")]
    expected = {"EMAIL": Counts(fn=1), "PHONE": Counts(fp=1)}
    assert overlap_counts(gold, predicted) == expected
    assert strict_counts(gold, predicted) == expected


def test_labels_only_in_gold_or_only_in_predictions_are_reported() -> None:
    gold = [A(0, 3, "ONLY_GOLD")]
    predicted = [A(10, 13, "ONLY_PRED")]
    result = overlap_counts(gold, predicted)
    assert result == {"ONLY_GOLD": Counts(fn=1), "ONLY_PRED": Counts(fp=1)}


def test_empty_inputs_give_no_labels() -> None:
    assert overlap_counts([], []) == {}
    assert strict_counts([], []) == {}


def test_only_gold_gives_false_negatives_and_only_predictions_false_positives() -> None:
    assert overlap_counts([A(0, 2, "X"), A(3, 5, "X")], []) == {"X": Counts(fn=2)}
    assert overlap_counts([], [A(0, 2, "X")]) == {"X": Counts(fp=1)}


def test_strict_mode_needs_both_ends_equal() -> None:
    gold = [A(0, 10, "X")]
    assert strict_counts(gold, [A(0, 9, "X")]) == {"X": Counts(fp=1, fn=1)}
    assert strict_counts(gold, [A(1, 10, "X")]) == {"X": Counts(fp=1, fn=1)}


def test_matching_does_not_depend_on_input_order() -> None:
    gold = [A(20, 30, "X"), A(0, 10, "X")]
    predicted = [A(25, 28, "X"), A(2, 5, "X"), A(6, 8, "X")]
    assert overlap_counts(gold, predicted) == {"X": Counts(tp=2, fp=1)}
    assert overlap_counts(gold[::-1], predicted[::-1]) == {"X": Counts(tp=2, fp=1)}


# --- leaks -----------------------------------------------------------------------------

TEXT = "Tel 612 345 678 fin"
#       0123456789012345678
PHONE_GOLD = A(4, 15, "NUMERO_TELEFONO")  # "612 345 678"


def test_fully_covered_value_does_not_leak() -> None:
    assert leaks(TEXT, [PHONE_GOLD], [A(4, 15, "PHONE")]) == {"NUMERO_TELEFONO": (0, 1)}


def test_value_covered_by_a_prediction_of_a_wrong_label_does_not_leak() -> None:
    assert leaks(TEXT, [PHONE_GOLD], [A(0, 19, "EMAIL")]) == {"NUMERO_TELEFONO": (0, 1)}


def test_one_uncovered_alphanumeric_character_is_a_leak() -> None:
    assert leaks(TEXT, [PHONE_GOLD], [A(4, 14, "PHONE")]) == {"NUMERO_TELEFONO": (1, 1)}
    assert leaks(TEXT, [PHONE_GOLD], [A(5, 15, "PHONE")]) == {"NUMERO_TELEFONO": (1, 1)}


def test_an_uncovered_space_alone_is_not_a_leak() -> None:
    # The three predictions leave only the spaces at 7 and 11 uncovered.
    predicted = [A(4, 7, "PHONE"), A(8, 11, "PHONE"), A(12, 15, "PHONE")]
    assert leaks(TEXT, [PHONE_GOLD], predicted) == {"NUMERO_TELEFONO": (0, 1)}


def test_uncovered_punctuation_is_not_a_leak() -> None:
    text = "mail a.b@c.es"
    gold = [A(5, 13, "CORREO_ELECTRONICO")]
    predicted = [A(5, 6, "EMAIL"), A(7, 8, "EMAIL"), A(9, 10, "EMAIL"), A(11, 13, "EMAIL")]
    assert leaks(text, gold, predicted) == {"CORREO_ELECTRONICO": (0, 1)}


def test_two_partial_predictions_that_together_cover_the_value_do_not_leak() -> None:
    predicted = [A(4, 10, "PHONE"), A(9, 15, "ADDRESS")]
    assert leaks(TEXT, [PHONE_GOLD], predicted) == {"NUMERO_TELEFONO": (0, 1)}


def test_no_prediction_means_every_gold_value_leaks() -> None:
    gold = [PHONE_GOLD, A(0, 3, "OTRO")]
    assert leaks(TEXT, gold, []) == {"NUMERO_TELEFONO": (1, 1), "OTRO": (1, 1)}


def test_gold_value_without_alphanumerics_never_leaks() -> None:
    text = "a -/- b"
    assert leaks(text, [A(2, 5, "RARO")], []) == {"RARO": (0, 1)}


def test_accented_letters_count_as_alphanumeric() -> None:
    text = "Sra. Lucía"
    gold = [A(5, 10, "NOMBRE")]
    assert leaks(text, gold, [A(5, 8, "X")]) == {"NOMBRE": (1, 1)}  # "ía" uncovered


def test_leaks_are_counted_per_gold_label_with_totals() -> None:
    text = "aa bb cc dd"
    gold = [A(0, 2, "L1"), A(3, 5, "L1"), A(6, 8, "L2"), A(9, 11, "L2")]
    predicted = [A(0, 2, "P"), A(6, 8, "P"), A(9, 11, "P")]
    assert leaks(text, gold, predicted) == {"L1": (1, 2), "L2": (0, 2)}


def test_leaks_ignore_labels_that_appear_only_in_predictions() -> None:
    assert leaks(TEXT, [], [A(0, 3, "PHONE")]) == {}


def test_leaks_per_100() -> None:
    assert leaks_per_100(3, 200) == pytest.approx(1.5)
    assert leaks_per_100(1, 1) == pytest.approx(100.0)
    assert leaks_per_100(0, 7) == 0.0


def test_leaks_per_100_is_zero_without_gold_values() -> None:
    assert leaks_per_100(0, 0) == 0.0


# --- latency ---------------------------------------------------------------------------

MS = 1_000_000  # nanoseconds per millisecond


def test_latency_of_one_to_a_hundred_milliseconds() -> None:
    p50, p95 = latency_ms([i * MS for i in range(1, 101)])
    assert p50 == pytest.approx(50.5)
    assert p95 == pytest.approx(95.05)


def test_latency_of_four_samples_interpolates_inclusively() -> None:
    p50, p95 = latency_ms([40 * MS, 10 * MS, 30 * MS, 20 * MS])
    assert p50 == pytest.approx(25.0)
    assert p95 == pytest.approx(38.5)


def test_latency_of_a_single_sample_is_that_sample() -> None:
    p50, p95 = latency_ms([7 * MS])
    assert p50 == pytest.approx(7.0)
    assert p95 == pytest.approx(7.0)


def test_latency_converts_nanoseconds_to_milliseconds() -> None:
    p50, _ = latency_ms([1_500_000, 1_500_000])
    assert p50 == pytest.approx(1.5)


def test_latency_of_no_samples_raises() -> None:
    with pytest.raises(ValueError):
        latency_ms([])
