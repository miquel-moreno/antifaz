"""Bench with the NER: threshold chosen on dev, test measured once, cold and warm latency.

The NER here is the dictionary fake in the test process: no model, no torch.
"""

import json
from pathlib import Path

import pytest
from evals.datasets.meddocan import MEDDOCAN_TO_ANTIFAZ_NER, Document
from evals.metrics import Annotation
from evals.run import (
    NER_END,
    NER_START,
    choose_threshold,
    render_ner_markdown,
    rss_mb,
    run_ner_bench,
    selection_row,
    to_json,
)

from antifaz.detect.ner.engine import NerDetector
from antifaz.detect.ner.fake import FakeBackend
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


def _detector_with_score(threshold: float) -> NerDetector:
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

    return NerDetector(InProcess(Backend({})), threshold=threshold)  # type: ignore[arg-type]


def _row(threshold: float, covered: float, person: float, address: float) -> dict[str, object]:
    return {
        "threshold": threshold,
        "covered_leaks_per_100": covered,
        "person_precision": person,
        "address_precision": address,
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


def test_no_threshold_above_the_floor_is_an_error() -> None:
    with pytest.raises(ValueError, match="floor"):
        choose_threshold([_row(0.3, 1.0, 0.5, 0.5)], floor=0.85)


def test_run_ner_bench_chooses_on_dev_and_measures_test_cold_and_warm() -> None:
    made: list[float] = []

    def make(threshold: float) -> NerDetector:
        made.append(threshold)
        return _detector_with_score(threshold)

    result = run_ner_bench(DOCS, DOCS, make, thresholds=(0.3, 0.4, 0.5), floor=0.5)

    assert [row["threshold"] for row in result.selection] == [0.3, 0.4, 0.5]
    # 0.3 also calls "Inventada" a person (a false positive); 0.4 finds both names.
    assert result.threshold == 0.4
    assert made == [0.3, 0.4, 0.5, 0.4]  # test measured once, with the chosen threshold
    person = result.report.by_type["PERSON"]
    assert (person["tp"], person["fp"], person["fn"]) == (2, 0, 0)
    assert result.report.by_source_label["NOMBRE_PERSONAL_SANITARIO"]["leaks"] == 0
    assert set(result.warm_latency_ms) == {"p50", "p95"}


def test_selection_rows_carry_only_numbers() -> None:
    row = selection_row(0.5, run_ner_bench(DOCS, DOCS, _detector_with_score, (0.5,), 0.0).report)
    assert set(row) == {
        "threshold",
        "leaks_per_100",
        "covered_leaks_per_100",
        "person_precision",
        "person_recall",
        "address_precision",
        "address_recall",
    }
    assert all(isinstance(value, float) for value in row.values())


def test_the_ner_section_compares_with_and_without_ner_without_any_value() -> None:
    from evals.run import evaluate

    base = evaluate(DOCS, MEDDOCAN_TO_ANTIFAZ_NER)
    result = run_ner_bench(DOCS, DOCS, _detector_with_score, (0.4, 0.5), 0.5)
    result.report.ner = {
        "threshold": result.threshold,
        "selection": result.selection,
        "latency_warm_ms": result.warm_latency_ms,
        "worker_rss_mb": 1234.5,
        "model": "example/model",
        "revision": "0" * 40,
        "manifest_sha256": "f" * 64,
    }

    markdown = render_ner_markdown(result.report, base)
    output = markdown + to_json(result.report)

    assert "## Con NER" in markdown
    assert "| 0.4 |" in markdown and "1234" in markdown
    for value in (NAME, DOCTOR, "Inventada"):
        assert value not in output
    assert json.loads(to_json(result.report))["ner"]["threshold"] == 0.4


def test_the_benchmark_doc_has_the_ner_markers() -> None:
    doc = (Path(__file__).resolve().parents[3] / "docs" / "benchmark.md").read_text("utf-8")
    assert NER_START in doc and NER_END in doc


def test_rss_of_this_process_is_measured_with_os_tools() -> None:
    import os

    value = rss_mb(os.getpid())
    assert value is None or value > 1
