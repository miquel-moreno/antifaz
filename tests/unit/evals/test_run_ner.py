"""Bench with the NER: threshold chosen on dev, test measured once, cold and warm latency.

The NER here is the dictionary fake in the test process: no model, no torch.
"""

import json
from pathlib import Path

import pytest
from evals.datasets.meddocan import MEDDOCAN_TO_ANTIFAZ_NER, Document
from evals.metrics import Annotation
from evals.run import (
    FLOOR_NOT_MET,
    NER_END,
    NER_START,
    SELECTION_RULE,
    SELECTION_RULE_CHANGED,
    NoThresholdError,
    ScoreCache,
    choose_threshold,
    dev_selection,
    evaluate,
    render_ner_markdown,
    rss_mb,
    run_ner_bench,
    select_threshold,
    selection_row,
    to_json,
)

from antifaz.detect.ner.engine import NerDetector
from antifaz.detect.ner.fake import FakeBackend
from antifaz.detect.scan import Scanner
from tests.nerfakes import InProcess

A = Annotation
NAME = "Lucía Prueba"
DOCTOR = "Marc Inventat"
TEXT = f"Paciente {NAME}, atendida por el doctor {DOCTOR} en la Calle Inventada 5.\n"
DOCS = [
    Document(
        "caso-1",
        TEXT,
        (
            A(TEXT.index(NAME), TEXT.index(NAME) + len(NAME), "NOMBRE_SUJETO_ASISTENCIA"),
            A(TEXT.index(DOCTOR), TEXT.index(DOCTOR) + len(DOCTOR), "NOMBRE_PERSONAL_SANITARIO"),
            A(TEXT.index("Calle"), TEXT.index("5.") + 1, "CALLE"),
        ),
    )
]


def _predictor() -> InProcess:
    """Finds the patient (score 0.9) always; the doctor only below 0.5 (a weaker "model")."""

    class Backend(FakeBackend):
        def predict(
            self, texts: list[str], labels: list[str], threshold: float
        ) -> list[list[list[object]]]:
            out = []
            for text in texts:
                found: list[list[object]] = []
                for value, score in ((NAME, 0.9), (DOCTOR, 0.45), ("Inventada", 0.35)):
                    start = text.find(value)
                    if start != -1 and score >= threshold:
                        found.append([start, start + len(value), "person", score])
                out.append(found)
            return out

    return InProcess(Backend({}))


def _row(
    threshold: float,
    covered: float,
    person: float,
    address: float,
    person_by_type: float = 0.0,
    address_by_type: float = 0.0,
) -> dict[str, object]:
    """person / address: precision against any personal data (the one the floor uses)."""
    return {
        "threshold": threshold,
        "covered_leaks_per_100": covered,
        "PERSON_any_pii_precision": person,
        "ADDRESS_any_pii_precision": address,
        "PERSON_precision": person_by_type,
        "ADDRESS_precision": address_by_type,
    }


def test_the_threshold_with_fewest_leaks_above_the_precision_floor_wins() -> None:
    rows = [
        _row(0.3, 10.0, 0.70, 0.95),  # fewest leaks, but PERSON precision under the floor
        _row(0.4, 12.0, 0.90, 0.95),
        _row(0.5, 14.0, 0.95, 0.95),
    ]
    assert choose_threshold(rows, floor=0.85) == 0.4


def test_a_tie_goes_to_the_higher_threshold() -> None:
    rows = [_row(0.4, 12.0, 0.90, 0.90), _row(0.5, 12.0, 0.92, 0.90)]
    assert choose_threshold(rows, floor=0.85) == 0.5


def test_no_threshold_above_the_floor_is_an_error_that_names_the_failing_type() -> None:
    rows = [_row(0.3, 1.0, 0.5, 0.9), _row(0.6, 2.0, 0.8, 0.9)]
    with pytest.raises(NoThresholdError, match="floor") as error:
        choose_threshold(rows, floor=0.85)
    assert "PERSON" in str(error.value) and "ADDRESS" not in str(error.value)
    assert "any personal data" in str(error.value)


def test_the_floor_is_measured_against_any_personal_data_not_by_type() -> None:
    # The dev case of 2026-10-01: by type under the floor, against any personal data above.
    rows = [
        _row(0.3, 10.0, 0.80, 0.90, person_by_type=0.63, address_by_type=0.56),
        _row(0.4, 11.0, 0.88, 0.86, person_by_type=0.66, address_by_type=0.57),
        _row(0.5, 12.0, 0.95, 0.90, person_by_type=0.69, address_by_type=0.58),
    ]
    assert choose_threshold(rows, floor=0.85) == 0.4


def test_the_selection_rule_and_its_late_change_are_declared() -> None:
    assert "any annotated personal data" in SELECTION_RULE
    assert "85 %" in SELECTION_RULE and "higher threshold" in SELECTION_RULE
    for text in ("2026-10-01", "after the first dev run", "63\u201369 %", "56\u201358 %"):
        assert text in SELECTION_RULE_CHANGED


def test_select_threshold_uses_the_rule_when_the_floor_is_met() -> None:
    rows = [_row(0.3, 10.0, 0.70, 0.95), _row(0.4, 12.0, 0.90, 0.95)]
    assert select_threshold(rows, floor=0.85) == (0.4, True)


def test_floor_not_met_falls_back_to_the_most_precise_threshold() -> None:
    # The dev case of run 2: ADDRESS passes, PERSON never does. Leaks do not matter here.
    rows = [
        _row(0.3, 12.3, 0.675, 0.959),
        _row(0.4, 12.5, 0.687, 0.959),
        _row(0.5, 13.0, 0.695, 0.962),
        _row(0.6, 13.8, 0.709, 0.963),
    ]
    assert select_threshold(rows, floor=0.85) == (0.6, False)


def test_the_fallback_looks_at_the_weakest_floor_type_and_a_tie_goes_higher() -> None:
    rows = [
        _row(0.3, 1.0, 0.70, 0.95),
        _row(0.4, 1.0, 0.80, 0.60),  # better PERSON, but ADDRESS is now the weak one
        _row(0.5, 1.0, 0.70, 0.90),
    ]
    assert select_threshold(rows, floor=0.85) == (0.5, False)


def test_the_selection_rule_declares_the_fallback() -> None:
    assert "floor not met" in SELECTION_RULE
    assert "NER not recommended by default" in SELECTION_RULE


def test_run_ner_bench_measures_test_even_when_the_floor_is_not_met() -> None:
    predictor = _predictor()
    result = run_ner_bench(DOCS, DOCS, predictor, thresholds=(0.3, 0.4), floor=1.01)
    assert result.floor_met is False
    assert result.threshold in (0.3, 0.4)
    assert result.report.documents == len(DOCS)


def test_run_ner_bench_chooses_on_dev_and_measures_test_cold_and_warm() -> None:
    predictor = _predictor()

    result = run_ner_bench(DOCS, DOCS, predictor, thresholds=(0.3, 0.4, 0.5), floor=0.5)

    assert [row["threshold"] for row in result.selection] == [0.3, 0.4, 0.5]
    # 0.3 also calls "Inventada" a person: a false positive by type, but it is inside the
    # annotated street, so not against any personal data; it leaks the same as 0.4, and the
    # tie goes to the higher threshold. 0.4 finds both names.
    assert result.threshold == 0.4
    assert result.floor_met is True
    # dev scored once for every threshold; test measured once (the warm pass hits the cache)
    assert predictor.calls == 2
    person = result.report.by_type["PERSON"]
    assert (person["tp"], person["fp"], person["fn"]) == (2, 0, 0)
    assert result.report.by_source_label["NOMBRE_PERSONAL_SANITARIO"]["leaks"] == 0
    assert set(result.warm_latency_ms) == {"p50", "p95"}


def test_selection_rows_give_both_types_with_overlap_and_strict_metrics() -> None:
    row = selection_row(0.5, evaluate(DOCS, MEDDOCAN_TO_ANTIFAZ_NER))
    for entity in ("PERSON", "ADDRESS"):
        for metric in (
            "precision",
            "recall",
            "strict_precision",
            "strict_recall",
            "any_pii_precision",
        ):
            assert isinstance(row[f"{entity}_{metric}"], float)
        assert isinstance(row[f"{entity}_gold"], int)
        assert isinstance(row[f"{entity}_non_pii_fp"], int)
        assert isinstance(row[f"{entity}_meets_floor"], bool)
        assert isinstance(row[f"{entity}_meets_floor_by_type"], bool)
    assert row["documents"] == 1
    assert {"threshold", "leaks_per_100", "covered_leaks_per_100"} <= set(row)


def _scored_backend() -> FakeBackend:
    """Overlapping entities with scores around every candidate threshold."""

    class Backend(FakeBackend):
        def predict(
            self, texts: list[str], labels: list[str], threshold: float
        ) -> list[list[list[object]]]:
            self.calls = getattr(self, "calls", 0) + 1
            out = []
            for text in texts:
                found: list[list[object]] = []
                for value, label, score in (
                    (NAME, "person", 0.92),
                    ("Lucía", "person", 0.31),
                    (DOCTOR, "person", 0.45),
                    ("doctor Marc", "person", 0.55),
                    ("Calle Inventada", "address", 0.62),
                    ("Inventada 5", "address", 0.38),
                ):
                    start = text.find(value)
                    if start != -1 and score >= threshold and label in labels:
                        found.append([start, start + len(value), label, score])
                out.append(sorted(found, key=lambda e: (e[0], e[1])))
            return out

    return Backend({})


def test_scoring_once_gives_the_same_spans_as_running_every_threshold() -> None:
    thresholds = (0.3, 0.4, 0.5, 0.6)
    backend = _scored_backend()
    cache = ScoreCache(InProcess(backend), min(thresholds))
    for threshold in thresholds:
        once = Scanner(NerDetector(cache, threshold=threshold))
        every = Scanner(NerDetector(InProcess(_scored_backend()), threshold=threshold))
        for document in DOCS:
            assert once(document.text) == every(document.text), threshold
    assert getattr(backend, "calls", 0) == 1  # every window scored once


def test_the_score_cache_refuses_a_threshold_below_its_own() -> None:
    cache = ScoreCache(InProcess(_scored_backend()), 0.3)
    with pytest.raises(ValueError, match="threshold"):
        cache.predict(["x"], ["person"], 0.2)


def test_dev_selection_scores_each_window_once_and_keeps_every_row() -> None:
    backend = _scored_backend()
    predictor = InProcess(backend)
    rows = dev_selection(DOCS, predictor, (0.3, 0.4, 0.5, 0.6), model_id="m")
    assert [row["threshold"] for row in rows] == [0.3, 0.4, 0.5, 0.6]
    assert predictor.calls == 1


def test_the_ner_section_compares_with_and_without_ner_without_any_value() -> None:
    from evals.run import evaluate

    base = evaluate(DOCS, MEDDOCAN_TO_ANTIFAZ_NER)
    result = run_ner_bench(DOCS, DOCS, _predictor(), (0.4, 0.5), 0.5)
    result.report.ner = {
        "threshold": result.threshold,
        "selection": result.selection,
        "latency_warm_ms": result.warm_latency_ms,
        "worker_rss_mb": 1234.5,
        "model": "example/model",
        "revision": "0" * 40,
        "manifest_sha256": "f" * 64,
        "floor_met": True,
    }

    markdown = render_ner_markdown(result.report, base)
    output = markdown + to_json(result.report)

    assert "## Con NER" in markdown
    assert "cualquier dato personal" in markdown and "mismo tipo" in markdown
    assert "2026-10-01" in markdown  # the late change of the rule is declared
    assert FLOOR_NOT_MET not in markdown
    assert "| 0.4 |" in markdown and "1234" in markdown
    for value in (NAME, DOCTOR, "Inventada"):
        assert value not in output
    assert json.loads(to_json(result.report))["ner"]["threshold"] == 0.4


def test_the_ner_section_says_clearly_when_the_floor_is_not_met() -> None:
    from evals.run import evaluate

    base = evaluate(DOCS, MEDDOCAN_TO_ANTIFAZ_NER)
    result = run_ner_bench(DOCS, DOCS, _predictor(), (0.4, 0.5), 1.01)
    result.report.ner = {
        "threshold": result.threshold,
        "floor_met": result.floor_met,
        "selection": result.selection,
    }
    markdown = render_ner_markdown(result.report, base)
    assert FLOOR_NOT_MET in markdown
    assert FLOOR_NOT_MET == (
        "El NER no cumple el suelo de precisión del 85 % en nombres; se publica como "
        "opcional y desactivado por defecto."
    )
    assert json.loads(to_json(result.report))["ner"]["floor_met"] is False


def test_the_benchmark_doc_has_the_ner_markers() -> None:
    doc = (Path(__file__).resolve().parents[3] / "docs" / "benchmark.md").read_text("utf-8")
    assert NER_START in doc and NER_END in doc


def test_rss_of_this_process_is_measured_with_os_tools() -> None:
    import os

    value = rss_mb(os.getpid())
    assert value is None or value > 1


def test_select_threshold_without_rows_is_an_error() -> None:
    with pytest.raises(NoThresholdError):
        select_threshold([], floor=0.85)
