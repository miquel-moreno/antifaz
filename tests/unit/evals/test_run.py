"""Bench runner: evaluate() numbers, the report never carries document values, doc update.

The dataset and the detector output are invented; the fake detector returns chosen Spans.
"""

import json
from pathlib import Path

import pytest
from evals.datasets.meddocan import MEDDOCAN_TO_ANTIFAZ, Document
from evals.metrics import Annotation
from evals.run import Report, evaluate, render_markdown, to_json, update_benchmark_doc

from antifaz.detect.types import Confidence, EntityType, Layer, Span

A = Annotation
T = EntityType

TEXT_1 = "Correo ana.test@example.com y tel 612345678. Paciente Lucía Prueba.\n"
TEXT_2 = "Fax 698765432 en Calle Inventada 5.\n"

DOCS = [
    Document(
        name="caso-1",
        text=TEXT_1,
        annotations=(
            A(7, 27, "CORREO_ELECTRONICO"),  # ana.test@example.com
            A(34, 43, "NUMERO_TELEFONO"),  # 612345678
            A(54, 66, "NOMBRE_SUJETO_ASISTENCIA"),  # Lucía Prueba (not covered by Antifaz)
        ),
    ),
    Document(
        name="caso-2",
        text=TEXT_2,
        annotations=(
            A(4, 13, "NUMERO_FAX"),  # 698765432
            A(17, 34, "CALLE"),  # Calle Inventada 5
        ),
    ),
]

VALUES = [
    "ana.test@example.com",
    "612345678",
    "612345",
    "Lucía Prueba",
    "Lucía",
    "Prueba",
    "698765432",
    "Calle Inventada",
    "Inventada",
    "Paciente",
]


def _span(start: int, end: int, entity: EntityType) -> Span:
    return Span(start, end, entity, Layer.PATTERN, Confidence.HIGH)


FAKE_OUTPUT = {
    TEXT_1: [
        _span(7, 27, T.EMAIL),  # exact
        _span(34, 40, T.PHONE),  # partial: "678" leaks
        _span(45, 53, T.EMAIL),  # false positive over "Paciente"
    ],
    TEXT_2: [
        _span(17, 34, T.ADDRESS),  # exact; the fax is missed
    ],
}


def fake_detect(text: str) -> list[Span]:
    return list(FAKE_OUTPUT[text])


@pytest.fixture
def report() -> Report:
    return evaluate(DOCS, MEDDOCAN_TO_ANTIFAZ, detect=fake_detect)


# --- evaluate --------------------------------------------------------------------------


def test_evaluate_counts_the_documents(report: Report) -> None:
    assert report.documents == 2
    assert isinstance(report.dataset, str)
    assert report.dataset


def test_evaluate_gives_overlap_metrics_per_antifaz_type(report: Report) -> None:
    email = report.by_type["EMAIL"]
    assert (email["tp"], email["fp"], email["fn"]) == (1, 1, 0)
    assert email["precision"] == pytest.approx(0.5)
    assert email["recall"] == pytest.approx(1.0)
    assert email["f1"] == pytest.approx(2 / 3)

    # Telephone and fax both map to PHONE: the partial one is a TP, the fax a FN.
    phone = report.by_type["PHONE"]
    assert (phone["tp"], phone["fp"], phone["fn"]) == (1, 0, 1)
    assert phone["precision"] == pytest.approx(1.0)
    assert phone["recall"] == pytest.approx(0.5)
    assert phone["f1"] == pytest.approx(2 / 3)

    address = report.by_type["ADDRESS"]
    assert (address["tp"], address["fp"], address["fn"]) == (1, 0, 0)
    assert address["f1"] == pytest.approx(1.0)


def test_evaluate_gives_strict_metrics_per_antifaz_type(report: Report) -> None:
    assert report.by_type["EMAIL"]["strict_precision"] == pytest.approx(0.5)
    assert report.by_type["EMAIL"]["strict_recall"] == pytest.approx(1.0)
    # The partial phone does not count in strict mode.
    assert report.by_type["PHONE"]["strict_recall"] == pytest.approx(0.0)
    assert report.by_type["PHONE"]["strict_f1"] == pytest.approx(0.0)
    assert report.by_type["ADDRESS"]["strict_f1"] == pytest.approx(1.0)


def test_evaluate_gives_precision_against_any_personal_data(report: Report) -> None:
    # The EMAIL over "Paciente" masks text that is not personal data: a real FP both ways.
    email = report.by_type["EMAIL"]
    assert email["any_pii_precision"] == pytest.approx(0.5)
    assert email["non_pii_fp"] == 1
    assert report.by_type["ADDRESS"]["any_pii_precision"] == pytest.approx(1.0)
    assert report.by_type["ADDRESS"]["non_pii_fp"] == 0


def test_a_prediction_over_unmapped_personal_data_is_not_a_non_pii_false_positive() -> None:
    def detect(text: str) -> list[Span]:
        if text == TEXT_1:  # an EMAIL over the patient name, a label with no Antifaz type
            return [_span(7, 27, T.EMAIL), _span(54, 66, T.EMAIL)]
        return []

    email = evaluate(DOCS, MEDDOCAN_TO_ANTIFAZ, detect=detect).by_type["EMAIL"]
    assert email["precision"] == pytest.approx(0.5)  # by type: the name is not an email
    assert email["any_pii_precision"] == pytest.approx(1.0)  # but it is personal data
    assert email["non_pii_fp"] == 0


def test_unmapped_labels_do_not_create_antifaz_types(report: Report) -> None:
    assert "NOMBRE_SUJETO_ASISTENCIA" not in report.by_type
    assert "None" not in report.by_type


def test_evaluate_gives_leaks_per_source_label(report: Report) -> None:
    by_label = report.by_source_label
    assert set(by_label) >= {
        "CORREO_ELECTRONICO",
        "NUMERO_TELEFONO",
        "NUMERO_FAX",
        "CALLE",
        "NOMBRE_SUJETO_ASISTENCIA",
    }
    expected_leaks = {
        "CORREO_ELECTRONICO": 0,
        "NUMERO_TELEFONO": 1,
        "NUMERO_FAX": 1,
        "CALLE": 0,
        "NOMBRE_SUJETO_ASISTENCIA": 1,
    }
    for label, leaked in expected_leaks.items():
        assert by_label[label]["n"] == 1, label
        assert by_label[label]["leaks"] == leaked, label
        assert by_label[label]["leaks_per_100"] == pytest.approx(100.0 * leaked), label


def test_source_labels_carry_their_antifaz_type_and_recall(report: Report) -> None:
    by_label = report.by_source_label
    assert by_label["CORREO_ELECTRONICO"]["type"] == "EMAIL"
    assert by_label["NUMERO_TELEFONO"]["type"] == "PHONE"
    assert by_label["NUMERO_FAX"]["type"] == "PHONE"
    assert by_label["CALLE"]["type"] == "ADDRESS"
    assert by_label["CORREO_ELECTRONICO"]["recall"] == pytest.approx(1.0)
    assert by_label["NUMERO_TELEFONO"]["recall"] == pytest.approx(1.0)
    assert by_label["NUMERO_FAX"]["recall"] == pytest.approx(0.0)
    assert by_label["CALLE"]["recall"] == pytest.approx(1.0)


def test_unmapped_source_label_counts_for_leaks_but_has_no_quality_metrics(
    report: Report,
) -> None:
    name = report.by_source_label["NOMBRE_SUJETO_ASISTENCIA"]
    assert name["type"] is None
    for metric in ("precision", "recall", "f1"):
        assert name.get(metric) is None, metric


def test_evaluate_gives_overall_and_covered_leaks(report: Report) -> None:
    overall = report.overall
    assert overall["total"] == 5
    assert overall["leaked"] == 3
    assert overall["leaks_per_100"] == pytest.approx(60.0)
    # Covered = source labels mapped to an Antifaz type (the name is left out).
    assert overall["covered_total"] == 4
    assert overall["covered_leaked"] == 2
    assert overall["covered_leaks_per_100"] == pytest.approx(50.0)


def test_evaluate_measures_latency_and_environment(report: Report) -> None:
    assert set(report.latency_ms) >= {"p50", "p95"}
    assert 0.0 <= report.latency_ms["p50"] <= report.latency_ms["p95"]
    assert report.environment
    assert all(isinstance(v, str) for v in report.environment.values())


def test_evaluate_calls_detect_once_per_document() -> None:
    seen: list[str] = []

    def counting_detect(text: str) -> list[Span]:
        seen.append(text)
        return fake_detect(text)

    evaluate(DOCS, MEDDOCAN_TO_ANTIFAZ, detect=counting_detect)
    # One untimed warm-up call on the first document, then one call per document.
    assert sorted(seen) == sorted([TEXT_1, TEXT_1, TEXT_2])


def test_a_prediction_over_an_unmapped_value_protects_it_from_leaking() -> None:
    def detect_name_as_address(text: str) -> list[Span]:
        return [_span(54, 66, T.ADDRESS)] if text == TEXT_1 else []

    result = evaluate(DOCS[:1], MEDDOCAN_TO_ANTIFAZ, detect=detect_name_as_address)
    assert result.by_source_label["NOMBRE_SUJETO_ASISTENCIA"]["leaks"] == 0
    # ...but for the metrics it is a false positive of ADDRESS.
    assert result.by_type["ADDRESS"]["fp"] == 1


# --- report output ---------------------------------------------------------------------


def test_to_json_is_valid_json_with_the_report_keys(report: Report) -> None:
    data = json.loads(to_json(report))
    assert set(data) >= {
        "dataset",
        "documents",
        "by_source_label",
        "by_type",
        "overall",
        "latency_ms",
        "environment",
    }
    assert data["documents"] == 2
    assert data["overall"]["leaked"] == 3


def test_report_outputs_never_contain_document_text_or_values(report: Report) -> None:
    outputs = {"json": to_json(report), "markdown": render_markdown(report)}
    for kind, output in outputs.items():
        for text in (TEXT_1, TEXT_2, TEXT_1.strip(), TEXT_2.strip()):
            assert text not in output, kind
        for value in VALUES:
            assert value not in output, (kind, value)


def test_render_markdown_has_the_three_sections(report: Report) -> None:
    lines = render_markdown(report).splitlines()
    assert "## Tipos cubiertos" in lines
    assert "## Tipos aún no cubiertos" in lines
    assert "## Global" in lines


def test_render_markdown_publishes_every_precision(report: Report) -> None:
    markdown = render_markdown(report)
    for header in (
        "Precisión (mismo tipo)",
        "Precisión estricta",
        "Precisión (cualquier dato personal)",
        "Tapan texto no personal",
    ):
        assert header in markdown


def test_render_markdown_puts_each_label_in_its_section(report: Report) -> None:
    markdown = render_markdown(report)
    covered = markdown.index("## Tipos cubiertos")
    not_covered = markdown.index("## Tipos aún no cubiertos")
    assert covered < not_covered
    covered_section = markdown[covered:not_covered]
    rest = markdown[not_covered:]
    assert "CORREO_ELECTRONICO" in covered_section
    assert "NOMBRE_SUJETO_ASISTENCIA" not in covered_section
    assert "NOMBRE_SUJETO_ASISTENCIA" in rest


# --- update_benchmark_doc --------------------------------------------------------------

DOC_BEFORE = "# Benchmark\n\nintro\n<!-- bench:start -->\nvieja tabla\n<!-- bench:end -->\nfin\n"
DOC_AFTER = "# Benchmark\n\nintro\n<!-- bench:start -->\n| a | b |\n<!-- bench:end -->\nfin\n"


def test_update_benchmark_doc_replaces_only_between_the_markers(tmp_path: Path) -> None:
    doc = tmp_path / "BENCHMARK.md"
    doc.write_bytes(DOC_BEFORE.encode("utf-8"))
    update_benchmark_doc(doc, "| a | b |\n")
    assert doc.read_bytes().decode("utf-8") == DOC_AFTER


def test_update_benchmark_doc_is_idempotent(tmp_path: Path) -> None:
    doc = tmp_path / "BENCHMARK.md"
    doc.write_bytes(DOC_BEFORE.encode("utf-8"))
    update_benchmark_doc(doc, "| a | b |")
    update_benchmark_doc(doc, "| a | b |")
    assert doc.read_bytes().decode("utf-8") == DOC_AFTER


@pytest.mark.parametrize(
    "content",
    [
        "# Benchmark\nsin marcadores\n",
        "# Benchmark\n<!-- bench:start -->\nsolo inicio\n",
        "# Benchmark\nsolo fin\n<!-- bench:end -->\n",
    ],
)
def test_update_benchmark_doc_without_markers_raises_and_keeps_the_file(
    tmp_path: Path, content: str
) -> None:
    doc = tmp_path / "BENCHMARK.md"
    doc.write_bytes(content.encode("utf-8"))
    with pytest.raises(ValueError):
        update_benchmark_doc(doc, "| a |")
    assert doc.read_bytes().decode("utf-8") == content


# Found by the code review of PR 3a: detections of types the dataset has no label for
# must be visible, or false positives would be hidden from the report.
def test_detections_of_types_without_a_dataset_label_are_reported() -> None:
    def detect(text: str) -> list[Span]:
        if text == TEXT_1:
            return [_span(54, 66, T.DATE_OF_BIRTH), _span(0, 6, T.ES_DNI)]
        return []

    result = evaluate(DOCS, MEDDOCAN_TO_ANTIFAZ, detect=detect)
    assert result.unmatched_types == {
        "DATE_OF_BIRTH": {"detections": 1, "overlapping_gold": 1},
        "ES_DNI": {"detections": 1, "overlapping_gold": 0},
    }
    markdown = render_markdown(result)
    assert "## Detecciones de tipos sin equivalente en el dataset" in markdown
    assert "| ES_DNI | 1 | 0 |" in markdown


def test_an_unknown_dataset_label_is_an_error() -> None:
    doc = Document("x", "abc", (Annotation(0, 3, "LABEL_NOT_IN_THE_MAPPING"),))
    with pytest.raises(ValueError, match="LABEL_NOT_IN_THE_MAPPING"):
        evaluate([doc], MEDDOCAN_TO_ANTIFAZ, detect=lambda _: [])


def test_the_synthetic_set_runs_end_to_end_without_values_in_the_output() -> None:
    from evals.generate import generate
    from evals.run import run_synthetic

    documents = generate(seed=3, counts={"email": 2, "payslip": 2, "trap": 2})
    report = run_synthetic(documents)
    assert report.documents == 6
    assert report.by_type["ES_DNI"]["recall"] > 0
    output = to_json(report) + render_markdown(report)
    for document in documents:
        for annotation in document.annotations:
            assert document.text[annotation.start : annotation.end] not in output
