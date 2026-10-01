"""Shared pieces of the NER tests: synthetic names and an in-process predictor.

The in-process predictor runs the fake backend in the test process, so unit tests of the
engine, the scanner and the masker stay fast. It exists only here: the gateway always runs the
NER in the process pool (ADR-0016).
"""

from collections.abc import Mapping, Sequence

from antifaz.detect.ner.backend import NerBackend
from antifaz.detect.ner.cache import SpanCache
from antifaz.detect.ner.engine import NerDetector
from antifaz.detect.ner.fake import FakeBackend

# Invented names, never real people.
CARMEN = "Carmen Prueba López"
JORDI = "Jordi Inventat Puig"
ANA = "Ana"
MARINA = "Marina"
NAMES: Mapping[str, str] = {CARMEN: "person", JORDI: "person", ANA: "person", MARINA: "person"}
ADDRESS = "Calle Ficticia 12"
FAKE_FACTORY = "antifaz.detect.ner.fake:create"


class InProcess:
    """A predictor that calls the backend directly and records what it was asked."""

    def __init__(self, backend: NerBackend) -> None:
        self.backend = backend
        self.calls = 0
        self.texts: list[str] = []
        self.started = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def close(self) -> None:
        self.closed = True

    def predict(
        self,
        texts: Sequence[str],
        labels: Sequence[str],
        threshold: float,
        deadline: float | None = None,  # the Predictor protocol; no timeout here, never passed
    ) -> object:
        self.calls += 1
        self.texts.extend(texts)
        return self.backend.predict(list(texts), list(labels), threshold)


def fake_detector(
    names: Mapping[str, str] = NAMES, **options: object
) -> tuple[NerDetector, InProcess]:
    predictor = InProcess(FakeBackend(names, triggers=False))
    options.setdefault("cache", SpanCache(100))
    return NerDetector(predictor, **options), predictor  # type: ignore[arg-type]  # **options is dict[str, object]
