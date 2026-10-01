"""scan() with the NER layer: normalised view, overlaps with patterns, optional."""

from collections.abc import Sequence

from antifaz.detect.ner.engine import NerDetector
from antifaz.detect.scan import Scanner, scan
from antifaz.detect.types import EntityType, Layer, Span
from tests.conftest import SENTINEL_DNI
from tests.nerfakes import CARMEN, fake_detector


def _values(text: str, spans: list[Span]) -> list[tuple[str, EntityType]]:
    return [(text[s.start : s.end], s.type) for s in spans]


class _Fixed:
    """A predictor that finds the same entities in every text."""

    def __init__(self, entities: list[list[object]]) -> None:
        self.entities = entities

    def start(self) -> None: ...
    def close(self) -> None: ...

    def predict(self, texts: Sequence[str], labels: Sequence[str], threshold: float) -> object:
        return [self.entities for _ in texts]


def test_without_ner_the_scanner_is_plain_scan() -> None:
    text = f"{CARMEN} con DNI {SENTINEL_DNI}"
    assert Scanner()(text) == scan(text)
    assert Scanner().scan_many([text, "hola"]) == [scan(text), []]


def test_the_scanner_joins_patterns_and_ner() -> None:
    ner, _ = fake_detector()
    text = f"{CARMEN} con DNI {SENTINEL_DNI}"
    assert _values(text, Scanner(ner)(text)) == [
        (CARMEN, EntityType.PERSON),
        (SENTINEL_DNI, EntityType.ES_DNI),
    ]


def test_the_ner_reads_the_normalised_view_and_spans_cover_the_original() -> None:
    ner, predictor = fake_detector({"Carmen Prueba Lopez": "person"})
    hidden = f"Carmen{chr(0x200B)} Prueba L{chr(0x43E)}{chr(0x301)}pez"  # ZWSP, Cyrillic o, accent
    text = f"Hola {hidden}!"
    spans = Scanner(ner)(text)
    assert predictor.texts == ["Hola Carmen Prueba Lopez!"]
    assert _values(text, spans) == [(hidden, EntityType.PERSON)]
    assert spans[0].layer is Layer.NER


def test_a_ner_span_containing_a_dni_keeps_the_dni_as_a_dni() -> None:
    text = f"Soy Carmen {SENTINEL_DNI}."
    # A model answer covering "Carmen 12345678Z" (fixed: the NER view hides digits glued to a
    # letter from the model, so the dictionary fake could not find it).
    ner = NerDetector(_Fixed([[4, 4 + len(f"Carmen {SENTINEL_DNI}"), "person", 0.9]]))
    assert _values(text, Scanner(ner)(text)) == [
        ("Carmen ", EntityType.PERSON),
        (SENTINEL_DNI, EntityType.ES_DNI),
    ]


def test_a_dni_beats_a_ner_span_that_overlaps_it_in_part_and_the_rest_is_kept() -> None:
    text = f"Soy Carmen {SENTINEL_DNI}."
    ner = NerDetector(_Fixed([[4, 15, "person", 0.9]]))  # "Carmen 1234": a badly cut name
    assert _values(text, Scanner(ner)(text)) == [
        ("Carmen ", EntityType.PERSON),
        (SENTINEL_DNI, EntityType.ES_DNI),
    ]


def test_scan_many_calls_the_ner_once_for_all_texts() -> None:
    ner, predictor = fake_detector()
    Scanner(ner).scan_many([CARMEN, "hola", f"adiós {CARMEN}"])
    assert predictor.calls == 1
