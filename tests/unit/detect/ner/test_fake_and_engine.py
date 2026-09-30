"""The fake NER backend and the engine in front of any predictor (chunks, checks, cache)."""

import time
from collections.abc import Sequence

import pytest

from antifaz.detect.ner.backend import load_factory
from antifaz.detect.ner.cache import SpanCache
from antifaz.detect.ner.engine import DEFAULT_LABELS, NerDetector
from antifaz.detect.ner.fake import FakeBackend, create
from antifaz.detect.types import Confidence, EntityType, Layer, Span
from antifaz.errors import DetectorFailed
from tests.nerfakes import ADDRESS, ANA, CARMEN, JORDI, InProcess, fake_detector


def _values(text: str, spans: Sequence[Span]) -> list[tuple[str, EntityType]]:
    return [(text[s.start : s.end], s.type) for s in spans]


# --- Fake backend ---------------------------------------------------------------------------


def test_fake_finds_its_names_with_word_boundaries_only() -> None:
    backend = FakeBackend({ANA: "person"}, triggers=False)
    text = "Ana vino. La semana pasada, Ana."
    (found,) = backend.predict([text], ["person"], 0.5)
    assert [text[s:e] for s, e, _, _ in found] == [ANA, ANA]  # type: ignore[misc]


def test_fake_skips_labels_not_asked_and_scores_below_the_threshold() -> None:
    backend = FakeBackend({ANA: "person", ADDRESS: "address"}, triggers=False, score=0.4)
    assert backend.predict([f"{ANA} {ADDRESS}"], ["address"], 0.3) == [[[4, 21, "address", 0.4]]]
    assert backend.predict([f"{ANA} {ADDRESS}"], ["address"], 0.5) == [[]]


def test_fake_triggers_are_off_unless_asked() -> None:
    backend = FakeBackend({}, triggers=False)
    assert backend.predict(["FAKE_RAISE FAKE_MALFORMED"], ["person"], 0.5) == [[]]


def test_fake_triggers_raise_and_return_malformed_output() -> None:
    backend = create(names={}, triggers=True)
    with pytest.raises(RuntimeError):
        backend.predict(["FAKE_RAISE"], ["person"], 0.5)
    assert backend.predict(["FAKE_MALFORMED"], ["person"], 0.5) == [[["bad"]]]


def test_factories_load_by_path_and_bad_paths_are_refused() -> None:
    assert load_factory("antifaz.detect.ner.fake:create") is create
    for path in ("antifaz.detect.ner.fake", "x:y:z", "no_such_module_xyz:create", "os:sep"):
        with pytest.raises(ValueError, match="factory"):
            load_factory(path)


# --- Engine --------------------------------------------------------------------------------


def test_the_engine_turns_backend_entities_into_ner_spans() -> None:
    detector, _ = fake_detector()
    text = f"Hola {CARMEN}, soy {JORDI}."
    (spans,) = detector.find_many([text])
    assert _values(text, spans) == [(CARMEN, EntityType.PERSON), (JORDI, EntityType.PERSON)]
    assert all(s.layer is Layer.NER and s.confidence is Confidence.MEDIUM for s in spans)


def test_addresses_map_to_the_address_type() -> None:
    detector, _ = fake_detector({ADDRESS: "address"})
    text = f"Vivo en {ADDRESS}."
    assert _values(text, detector.find_many([text])[0]) == [(ADDRESS, EntityType.ADDRESS)]
    assert DEFAULT_LABELS == {"person": EntityType.PERSON, "address": EntityType.ADDRESS}


def test_blank_texts_never_reach_the_backend() -> None:
    detector, predictor = fake_detector()
    assert detector.find_many(["", "  \n"]) == [[], []]
    assert predictor.calls == 0


def test_a_name_across_a_window_border_is_found_thanks_to_the_overlap() -> None:
    filler = " ".join(["palabra"] * 8)
    text = f"{filler} {CARMEN} {filler}"  # the name spans tokens 8 to 10
    found, _ = fake_detector(window=10, overlap=4)
    assert _values(text, found.find_many([text])[0]) == [(CARMEN, EntityType.PERSON)]
    missed, _ = fake_detector(window=10, overlap=0)  # without overlap the name is cut in two
    assert missed.find_many([text]) == [[]]


def test_one_predict_call_for_many_texts_and_chunks_in_batches() -> None:
    detector, predictor = fake_detector(window=4, overlap=1, batch_chunks=3)
    texts = [f"{CARMEN} uno dos tres cuatro cinco", "nada", f"soy {JORDI}"]
    spans = detector.find_many(texts)
    assert _values(texts[0], spans[0]) == [(CARMEN, EntityType.PERSON)]
    assert spans[1] == []
    assert _values(texts[2], spans[2]) == [(JORDI, EntityType.PERSON)]
    assert predictor.calls == 2  # 5 chunks in batches of 3


def test_a_cache_hit_skips_the_backend() -> None:
    detector, predictor = fake_detector()
    text = f"Hola {CARMEN}"
    first = detector.find_many([text])
    second = detector.find_many([text, text])
    assert predictor.calls == 1
    assert second == [first[0], first[0]]


def test_a_repeated_text_in_one_call_reaches_the_backend_once() -> None:
    detector, predictor = fake_detector()
    detector.find_many([CARMEN, CARMEN])
    assert predictor.texts == [CARMEN]


@pytest.mark.parametrize(
    "change",
    [{"threshold": 0.6}, {"labels": {"person": EntityType.PERSON}}, {"model_id": "other"}],
)
def test_the_cache_is_invalidated_by_threshold_labels_and_model(change: dict[str, object]) -> None:
    predictor = InProcess(FakeBackend({CARMEN: "person"}, triggers=False))
    cache = SpanCache(10)
    NerDetector(predictor, cache=cache).find_many([CARMEN])  # type: ignore[arg-type]
    NerDetector(predictor, cache=cache, **change).find_many([CARMEN])  # type: ignore[arg-type]
    assert predictor.calls == 2


def test_the_cache_respects_its_bound() -> None:
    detector, predictor = fake_detector(cache=SpanCache(1))
    detector.find_many(["uno"])
    detector.find_many(["dos"])
    detector.find_many(["uno"])
    assert predictor.calls == 3


def test_no_cache_means_every_call_reaches_the_backend() -> None:
    detector, predictor = fake_detector(cache=None)
    detector.find_many([CARMEN])
    detector.find_many([CARMEN])
    assert predictor.calls == 2


class _Answer:
    """A predictor that returns a fixed answer (malformed on purpose)."""

    def __init__(self, answer: object) -> None:
        self.answer = answer

    def start(self) -> None: ...
    def close(self) -> None: ...

    def predict(self, texts: Sequence[str], labels: Sequence[str], threshold: float) -> object:
        return self.answer


@pytest.mark.parametrize(
    "answer",
    [
        None,
        [],  # one result per chunk is required
        [[], []],
        [None],
        [[None]],
        [[[0, 3, "person"]]],
        [[[0.0, 3, "person", 0.9]]],
        [[[True, 3, "person", 0.9]]],
        [[[3, 3, "person", 0.9]]],
        [[[-1, 3, "person", 0.9]]],
        [[[0, 99, "person", 0.9]]],
        [[[0, 3, "organization", 0.9]]],
        [[[0, 3, "person", "0.9"]]],
        [[[0, 3, "person", 1.5]]],
        [[[0, 3, "person", False]]],
    ],
)
def test_malformed_backend_output_fails_closed(answer: object) -> None:
    detector = NerDetector(_Answer(answer))  # type: ignore[arg-type]
    with pytest.raises(DetectorFailed):
        detector.find_many(["Hola Carmen"])


def test_entities_below_the_threshold_are_ignored() -> None:
    detector = NerDetector(_Answer([[[0, 4, "person", 0.2]]]), threshold=0.5)  # type: ignore[arg-type]
    assert detector.find_many(["Hola Carmen"]) == [[]]


def test_start_and_close_go_to_the_predictor() -> None:
    detector, predictor = fake_detector()
    detector.start()
    detector.close()
    assert predictor.started and predictor.closed


@pytest.mark.parametrize(
    "options",
    [{"threshold": 0.0}, {"threshold": 1.1}, {"labels": {}}, {"batch_chunks": 0}],
)
def test_invalid_engine_settings_are_refused(options: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        fake_detector(**options)


# --- Limits per request (review of 6a) -------------------------------------------------------


class _Slow(InProcess):
    """A predictor with a time limit per request that takes `seconds` per call."""

    def __init__(self, seconds: float, timeout: float) -> None:
        super().__init__(FakeBackend({CARMEN: "person"}))
        self.seconds = seconds
        self.timeout = timeout
        self.deadlines: list[float] = []

    def predict(  # type: ignore[override]
        self, texts: Sequence[str], labels: Sequence[str], threshold: float, deadline: float
    ) -> list[list[list[object]]]:
        self.deadlines.append(deadline)
        time.sleep(self.seconds)
        return super().predict(texts, labels, threshold)


def test_too_many_windows_in_one_request_block_before_calling_the_backend() -> None:
    detector, predictor = fake_detector(window=2, overlap=0, max_chunks=3)
    with pytest.raises(DetectorFailed):
        detector.find_many(["uno dos tres cuatro", "cinco seis siete"])  # 4 windows
    assert predictor.calls == 0
    assert detector.find_many(["uno dos tres cuatro cinco seis"]) == [[]]  # 3 windows: fine


def test_the_whole_request_shares_one_deadline() -> None:
    slow = _Slow(seconds=0.2, timeout=0.5)
    detector = NerDetector(slow, window=2, overlap=0, batch_chunks=1)  # type: ignore[arg-type]
    began = time.monotonic()
    with pytest.raises(DetectorFailed):
        detector.find_many([" ".join(["palabra"] * 20)])  # 10 calls of 0.2 s
    assert time.monotonic() - began < 1.0
    assert len(set(slow.deadlines)) == 1  # the same deadline for every call
    assert slow.deadlines[0] - began <= 0.5 + 0.05


def test_an_explicit_request_timeout_wins_over_the_predictor() -> None:
    slow = _Slow(seconds=0.0, timeout=100.0)
    detector = NerDetector(slow, timeout=1.0)  # type: ignore[arg-type]
    began = time.monotonic()
    detector.find_many([CARMEN])
    assert slow.deadlines[0] - began <= 1.05


@pytest.mark.parametrize("options", [{"timeout": 0}, {"max_chunks": 0}])
def test_invalid_request_limits_are_refused(options: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        fake_detector(**options)
