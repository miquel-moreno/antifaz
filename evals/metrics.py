"""Benchmark metrics: overlap and strict counts, leaks and latency.

Every function works with positions and labels only; no text value ever leaves them.
"""

import statistics
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Annotation:
    """Half-open range [start, end) of a text with its label (gold or predicted)."""

    start: int
    end: int
    label: str


@dataclass(frozen=True, slots=True)
class Counts:
    """True positives, false positives and false negatives of one label."""

    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        """tp / (tp + fp), or 0.0 when there are no predictions."""
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def recall(self) -> float:
        """tp / (tp + fn), or 0.0 when there is no gold."""
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    @property
    def f1(self) -> float:
        """Harmonic mean of precision and recall, or 0.0 when both are 0."""
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0


def _by_label(annotations: Sequence[Annotation]) -> dict[str, list[Annotation]]:
    grouped: dict[str, list[Annotation]] = defaultdict(list)
    for annotation in sorted(annotations, key=lambda a: (a.start, a.end, a.label)):
        grouped[annotation.label].append(annotation)
    return grouped


def _count(
    gold: Sequence[Annotation],
    predicted: Sequence[Annotation],
    matches: Callable[[Annotation, Annotation], bool],
) -> dict[str, Counts]:
    gold_by_label, predicted_by_label = _by_label(gold), _by_label(predicted)
    result: dict[str, Counts] = {}
    for label in sorted(set(gold_by_label) | set(predicted_by_label)):
        unmatched = list(gold_by_label.get(label, []))
        tp = fp = 0
        for prediction in predicted_by_label.get(label, []):
            # Greedy, in order of position: the first unmatched gold that matches.
            hit = next((g for g in unmatched if matches(g, prediction)), None)
            if hit is None:
                fp += 1
            else:
                unmatched.remove(hit)
                tp += 1
        result[label] = Counts(tp=tp, fp=fp, fn=len(unmatched))
    return result


def overlap_counts(
    gold: Sequence[Annotation], predicted: Sequence[Annotation]
) -> dict[str, Counts]:
    """Per label: a prediction is a TP if it overlaps >= 1 character of a gold annotation
    of the same label. One-to-one greedy matching in order of position (each gold is
    matched once; extra overlapping predictions are FP); unmatched gold are FN. Labels
    seen only in gold or only in predictions are included. Independent of input order.
    Greedy matching can only give fewer hits than the best possible one, never more: it
    does not favour the detector."""
    return _count(gold, predicted, lambda g, p: g.start < p.end and p.start < g.end)


def strict_counts(gold: Sequence[Annotation], predicted: Sequence[Annotation]) -> dict[str, Counts]:
    """Same as overlap_counts, but a TP needs identical start and end."""
    return _count(gold, predicted, lambda g, p: (g.start, g.end) == (p.start, p.end))


def leaks(
    text: str, gold: Sequence[Annotation], predicted: Sequence[Annotation]
) -> dict[str, tuple[int, int]]:
    """Per gold label, (leaked, total). A gold value leaks if any alphanumeric character
    (str.isalnum) inside its range is not covered by a predicted span of ANY label.
    Uncovered non-alphanumeric characters do not count; a gold value without
    alphanumeric characters never leaks."""
    covered = bytearray(len(text) + 1)
    for prediction in predicted:
        covered[prediction.start : prediction.end] = b"\x01" * (prediction.end - prediction.start)
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for annotation in gold:
        leaked = any(
            text[i].isalnum() and not covered[i] for i in range(annotation.start, annotation.end)
        )
        totals[annotation.label][0] += int(leaked)
        totals[annotation.label][1] += 1
    return {label: (leaked, total) for label, (leaked, total) in totals.items()}


def leaks_per_100(leaked: int, total: int) -> float:
    """Leaked values per 100 gold values (0.0 when total == 0)."""
    return 100 * leaked / total if total else 0.0


def latency_ms(samples_ns: Sequence[int]) -> tuple[float, float]:
    """(p50, p95) in milliseconds. p50 is the median; p95 follows
    statistics.quantiles(..., n=100, method="inclusive"). A single sample gives that
    sample for both. Raises ValueError on empty input."""
    if not samples_ns:
        raise ValueError("no latency samples")
    ms = [sample / 1_000_000 for sample in samples_ns]
    if len(ms) == 1:
        return ms[0], ms[0]
    return statistics.median(ms), statistics.quantiles(ms, n=100, method="inclusive")[94]
