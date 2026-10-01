"""Presidio baseline (issue 13): the Spanish setup is checked, the mapping is explicit and the
comparison only shows the types both tools cover.

Presidio is NOT installed here (it lives in the `bench` dependency group): a fake analyzer stands
in for it. The documents and values are invented.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest
from evals import presidio_baseline as pb
from evals.datasets.meddocan import MEDDOCAN_TO_ANTIFAZ, MEDDOCAN_TO_ANTIFAZ_NER, Document
from evals.metrics import Annotation
from evals.run import (
    PRESIDIO_END,
    PRESIDIO_START,
    Report,
    evaluate,
    latest_report,
    load_report,
    main,
    render_presidio_markdown,
    run_presidio,
    shared_types,
    to_json,
)

from antifaz.detect.types import Confidence, EntityType, Layer, Span

A = Annotation
T = EntityType

TEXT_1 = "Correo ana.test@example.com y tel 612345678. Paciente Lucía Prueba.\n"
TEXT_2 = "Fax 698765432 en Calle Inventada 5. Dra. Marta Ficticia.\n"
DOCS = [
    Document(
        "caso-1",
        TEXT_1,
        (
            A(7, 27, "CORREO_ELECTRONICO"),  # ana.test@example.com
            A(34, 43, "NUMERO_TELEFONO"),  # 612345678
            A(54, 66, "NOMBRE_SUJETO_ASISTENCIA"),  # Lucía Prueba
        ),
    ),
    Document(
        "caso-2",
        TEXT_2,
        (
            A(4, 13, "NUMERO_FAX"),  # 698765432
            A(17, 34, "CALLE"),  # Calle Inventada 5
            A(41, 55, "NOMBRE_PERSONAL_SANITARIO"),  # Marta Ficticia
        ),
    ),
]
VALUES = ["ana.test@example.com", "612345678", "Lucía Prueba", "698765432", "Marta Ficticia"]


@dataclass(frozen=True)
class FakeResult:
    entity_type: str
    start: int
    end: int
    score: float = 0.85


@dataclass
class FakeAnalyzer:
    """Answers like Presidio's AnalyzerEngine.analyze, from a table; records the languages."""

    output: dict[str, list[FakeResult]]
    languages: list[str] = field(default_factory=list)

    def analyze(self, text: str, language: str) -> list[FakeResult]:
        self.languages.append(language)
        return list(self.output.get(text, []))


PRESIDIO_OUTPUT = {
    TEXT_1: [
        FakeResult("EMAIL_ADDRESS", 7, 27, 1.0),
        FakeResult("PHONE_NUMBER", 34, 43, 0.4),
        FakeResult("PERSON", 54, 66),
        FakeResult("LOCATION", 45, 53),  # "Paciente": no Antifaz equivalent
    ],
    TEXT_2: [
        FakeResult("PERSON", 41, 46),  # "Marta": partial, "Ficticia" leaks
        FakeResult("ES_NIF", 4, 13, 0.5),  # the fax read as a NIF: a type MEDDOCAN never has
    ],
}


def _span(start: int, end: int, entity: EntityType) -> Span:
    return Span(start, end, entity, Layer.PATTERN, Confidence.HIGH)


ANTIFAZ_OUTPUT = {
    TEXT_1: [_span(7, 27, T.EMAIL), _span(34, 43, T.PHONE)],
    TEXT_2: [_span(4, 13, T.PHONE), _span(17, 34, T.ADDRESS)],
}
NER_OUTPUT = {
    TEXT_1: [*ANTIFAZ_OUTPUT[TEXT_1], _span(54, 66, T.PERSON)],
    TEXT_2: [*ANTIFAZ_OUTPUT[TEXT_2], _span(41, 55, T.PERSON)],
}

VERSIONS = {
    "presidio-analyzer": "2.2.364",
    "spacy": "3.8.16",
    "xx-ent-wiki-sm": "3.8.0",
    "model_sha256": "a" * 64,
}


def _presidio_report() -> Report:
    return run_presidio(
        DOCS, FakeAnalyzer(PRESIDIO_OUTPUT), versions=VERSIONS, configuration=pb.configuration()
    )


def _base() -> Report:
    return evaluate(DOCS, MEDDOCAN_TO_ANTIFAZ, lambda text: ANTIFAZ_OUTPUT[text])


def _ner() -> Report:
    return evaluate(DOCS, MEDDOCAN_TO_ANTIFAZ_NER, lambda text: NER_OUTPUT[text])


# --- mapping and adapter ----------------------------------------------------------------


def test_presidio_types_map_to_antifaz_types_explicitly() -> None:
    assert pb.PRESIDIO_TO_ANTIFAZ == {
        "EMAIL_ADDRESS": T.EMAIL,
        "PHONE_NUMBER": T.PHONE,
        "IBAN_CODE": T.IBAN,
        "CREDIT_CARD": T.CREDIT_CARD,
        "IP_ADDRESS": T.IP,
        "ES_NIF": T.ES_DNI,
        "ES_NIE": T.ES_NIE,
        "ES_PASSPORT": T.ES_PASSPORT,
        "PERSON": T.PERSON,
        "LOCATION": None,
        "ORGANIZATION": None,
    }


def test_results_become_annotations_with_antifaz_types_and_unmapped_ones_keep_their_name() -> None:
    results = [FakeResult("ES_NIF", 0, 9), FakeResult("LOCATION", 10, 15, 0.1)]
    assert pb.to_annotations(results) == [A(0, 9, "ES_DNI"), A(10, 15, "LOCATION")]


def test_an_unknown_presidio_type_is_an_error_not_a_silent_label() -> None:
    with pytest.raises(pb.PresidioSetupError, match="DATE_TIME"):
        pb.to_annotations([FakeResult("DATE_TIME", 0, 4)])


def test_the_detector_always_asks_presidio_in_spanish() -> None:
    analyzer = FakeAnalyzer(PRESIDIO_OUTPUT)
    detect = pb.detector(analyzer)
    detect(TEXT_1)
    detect(TEXT_2)
    assert analyzer.languages == ["es", "es"]


def test_the_nlp_engine_uses_the_mit_multilingual_model_for_spanish_only() -> None:
    models = pb.NLP_CONFIGURATION["models"]
    assert models == [{"lang_code": "es", "model_name": "xx_ent_wiki_sm"}]
    assert "es_core_news" not in json.dumps(pb.NLP_CONFIGURATION)  # GPL-3.0 (ADR-0003)


def test_the_configuration_for_the_report_is_plain_json() -> None:
    configuration = pb.configuration()
    assert json.loads(json.dumps(configuration)) == configuration
    assert configuration["language"] == "es"
    assert configuration["phone_regions"] == ["ES"]
    assert configuration["score_threshold"] == 0.0
    assert "EsPassportRecognizer" in configuration["recognizers"]


# --- no silent fallback to English --------------------------------------------------------


def _engine(
    languages: tuple[str, ...] = ("es",),
    recognizer_languages: tuple[str, ...] = ("es", "es"),
    entities: frozenset[str] = pb.REQUIRED_ENTITIES,
    engine_languages: tuple[str, ...] = ("es",),
) -> SimpleNamespace:
    return SimpleNamespace(
        supported_languages=list(engine_languages),
        nlp_engine=SimpleNamespace(get_supported_languages=lambda: list(languages)),
        registry=SimpleNamespace(
            recognizers=[SimpleNamespace(supported_language=lang) for lang in recognizer_languages]
        ),
        get_supported_entities=lambda language: sorted(entities) if language == "es" else [],
    )


def test_a_full_spanish_setup_passes_the_check() -> None:
    pb.check_spanish(_engine())


@pytest.mark.parametrize("missing", ["ES_NIF", "ES_NIE", "ES_PASSPORT", "PERSON"])
def test_a_missing_spanish_recognizer_fails_loudly(missing: str) -> None:
    with pytest.raises(pb.PresidioSetupError, match=missing):
        pb.check_spanish(_engine(entities=pb.REQUIRED_ENTITIES - {missing}))


def test_an_nlp_engine_with_english_fails() -> None:
    with pytest.raises(pb.PresidioSetupError, match="NLP engine"):
        pb.check_spanish(_engine(languages=("en", "es")))


def test_an_analyzer_for_other_languages_fails() -> None:
    with pytest.raises(pb.PresidioSetupError, match="analyzer"):
        pb.check_spanish(_engine(engine_languages=("en",)))


def test_a_recognizer_for_english_fails() -> None:
    with pytest.raises(pb.PresidioSetupError, match="en"):
        pb.check_spanish(_engine(recognizer_languages=("es", "en")))


def test_the_model_must_be_the_pinned_multilingual_one() -> None:
    pb.check_model({"lang": "xx", "name": "ent_wiki_sm", "version": "3.8.0"})
    with pytest.raises(pb.PresidioSetupError, match="model"):
        pb.check_model({"lang": "es", "name": "core_news_sm", "version": "3.8.0"})
    with pytest.raises(pb.PresidioSetupError, match="model"):
        pb.check_model({"lang": "xx", "name": "ent_wiki_sm", "version": "3.7.0"})


def test_the_model_hash_comes_from_uv_lock() -> None:
    lock = (
        '[[package]]\nname = "xx-ent-wiki-sm"\nversion = "3.8.0"\n'
        'wheels = [\n    { url = "https://example.invalid/x.whl", hash = "sha256:'
        + "b" * 64
        + '" },\n]\n'
    )
    assert pb.locked_sha256(lock, "xx-ent-wiki-sm") == "b" * 64
    with pytest.raises(pb.PresidioSetupError):
        pb.locked_sha256(lock, "not-locked")


def test_the_real_lock_pins_the_model_by_hash() -> None:
    lock = (Path(__file__).resolve().parents[3] / "uv.lock").read_text(encoding="utf-8")
    assert len(pb.locked_sha256(lock, "xx-ent-wiki-sm")) == 64


# --- comparison -------------------------------------------------------------------------


def test_evaluate_accepts_annotations_from_a_tool_without_antifaz_types() -> None:
    report = _presidio_report()
    assert report.by_type["EMAIL"]["recall"] == 1.0
    assert report.by_type["PERSON"]["tp"] == 2
    assert report.unmatched_types["LOCATION"] == {"detections": 1, "overlapping_gold": 0}
    assert report.unmatched_types["ES_DNI"] == {"detections": 1, "overlapping_gold": 1}


def test_the_presidio_report_records_versions_and_configuration() -> None:
    report = _presidio_report()
    assert report.baseline["tool"] == "presidio-analyzer"
    assert report.baseline["versions"] == VERSIONS
    assert report.baseline["configuration"] == pb.configuration()
    assert report.baseline["shared_types"] == ["EMAIL", "PHONE", "PERSON"]
    assert report.dataset.startswith("MEDDOCAN test")


def test_shared_types_are_the_ones_both_cover_and_the_dataset_annotates() -> None:
    assert shared_types(MEDDOCAN_TO_ANTIFAZ_NER) == ["EMAIL", "PHONE", "PERSON"]
    # ES_NSS and ADDRESS: only Antifaz has them, so they are not compared.
    assert "ES_NSS" not in shared_types(MEDDOCAN_TO_ANTIFAZ_NER)


def test_the_comparison_has_every_shared_type_and_tool_without_any_value() -> None:
    presidio = _presidio_report()
    markdown = render_presidio_markdown(presidio, _base(), _ner(), "2026-10-01-0.1.0-ner.json")
    output = markdown + to_json(presidio)

    assert "## Comparación con Presidio" in markdown
    for name in ("Antifaz sin NER", "Antifaz con NER", "Presidio"):
        assert name in markdown
    for entity in ("EMAIL", "PHONE", "PERSON"):
        assert f"| {entity} |" in markdown
    assert "2.2.364" in markdown and "3.8.16" in markdown and "xx_ent_wiki_sm" in markdown
    assert "a" * 12 in markdown  # model hash (shortened)
    assert "2026-10-01-0.1.0-ner.json" in markdown
    for value in VALUES:
        assert value not in output


def test_person_is_not_applicable_to_antifaz_without_ner() -> None:
    markdown = render_presidio_markdown(_presidio_report(), _base(), _ner(), "x-ner.json")
    person = [line for line in markdown.splitlines() if line.startswith("| PERSON | Antifaz sin")]
    assert len(person) == 1 and "no aplica" in person[0]


def test_the_numbers_of_each_tool_are_its_own() -> None:
    markdown = render_presidio_markdown(_presidio_report(), _base(), _ner(), "x-ner.json")
    rows = {
        tuple(cell.strip() for cell in line.strip("|").split("|")[:2]): line
        for line in markdown.splitlines()
        if line.startswith("| ")
    }
    # Presidio found one of the two names fully and half of the other: 50.0 leaks per 100.
    assert "50.0" in rows[("PERSON", "Presidio")]
    # Antifaz without NER found the fax as PHONE; Presidio read it as a NIF (not PHONE).
    assert "100.0 %" in rows[("PHONE", "Antifaz sin NER")]
    assert "50.0 %" in rows[("PHONE", "Presidio")]


def test_without_ner_results_the_column_says_so() -> None:
    markdown = render_presidio_markdown(_presidio_report(), _base(), None, None)
    ner = [line for line in markdown.splitlines() if line.startswith("| EMAIL | Antifaz con NER")]
    assert len(ner) == 1 and "sin resultados" in ner[0]


def test_types_without_data_in_the_dataset_are_listed_as_not_applicable() -> None:
    markdown = render_presidio_markdown(_presidio_report(), _base(), _ner(), "x-ner.json")
    assert "No aplica" in markdown or "no aplica" in markdown
    for entity in ("IBAN", "CREDIT_CARD", "IP", "ES_DNI", "ES_NIE", "ES_PASSPORT"):
        assert f"| {entity} |" in markdown


def test_the_comparison_has_no_adjectives() -> None:
    markdown = render_presidio_markdown(_presidio_report(), _base(), _ner(), "x-ner.json")
    lowered = markdown.lower()
    for word in ("mejor", "peor", "superior", "inferior", "gana", "best", "better", "worse"):
        assert word not in lowered, word


def test_latency_of_every_tool_is_shown() -> None:
    markdown = render_presidio_markdown(_presidio_report(), _base(), _ner(), "x-ner.json")
    assert "p50" in markdown and "p95" in markdown
    assert markdown.count(" ms") >= 6


# --- committed results and the command line --------------------------------------------


def test_the_latest_ner_report_is_read_from_the_results(tmp_path: Path) -> None:
    for name in ("2026-09-30-0.1.0-ner.json", "2026-10-01-0.1.0-ner.json"):
        (tmp_path / name).write_text(to_json(_ner()), encoding="utf-8")
    (tmp_path / "2026-10-02-0.1.0-ner-dev.json").write_text("{}", encoding="utf-8")

    found = latest_report(tmp_path, "-ner")
    assert found is not None
    report, name = found
    assert name == "2026-10-01-0.1.0-ner.json"
    assert report.by_type == _ner().by_type
    assert latest_report(tmp_path, "-presidio") is None


def test_load_report_round_trips(tmp_path: Path) -> None:
    report = _presidio_report()
    path = tmp_path / "r.json"
    path.write_text(to_json(report), encoding="utf-8")
    assert load_report(path) == report


def test_presidio_cannot_be_combined_with_the_ner_run(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--presidio", "--ner"])
    assert "--presidio" in capsys.readouterr().err


def test_the_benchmark_doc_has_the_presidio_markers() -> None:
    doc = (Path(__file__).resolve().parents[3] / "docs" / "benchmark.md").read_text("utf-8")
    assert PRESIDIO_START in doc and PRESIDIO_END in doc
