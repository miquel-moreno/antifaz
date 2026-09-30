"""What a NER backend and a predictor look like, and how a backend factory is loaded.

A backend runs inside a worker process and answers, for each text, a list of entities written
as `[start, end, label, score]` (character offsets in that text). A predictor is what the
detector talks to in the gateway process: the process pool (or, in tests only, a backend
called in the same process).
"""

import importlib
import re
from collections.abc import Callable, Sequence
from typing import Protocol

# "package.module:function", nothing else (no attribute paths, no spaces).
_FACTORY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*")


class NerBackend(Protocol):
    """A loaded model. Runs only inside a worker process."""

    def predict(
        self, texts: list[str], labels: list[str], threshold: float
    ) -> Sequence[Sequence[Sequence[object]]]: ...


class Predictor(Protocol):
    """What the detector calls: one list of raw entities per text. Checked by the engine."""

    def start(self) -> None: ...

    def close(self) -> None: ...

    def predict(
        self,
        texts: Sequence[str],
        labels: Sequence[str],
        threshold: float,
        deadline: float | None = None,
    ) -> object:
        """`deadline` (time.monotonic()) is only passed to predictors with a `timeout`."""
        ...


def split_factory(path: str) -> tuple[str, str]:
    """("package.module", "function") of a factory path, or ValueError."""
    if not _FACTORY.fullmatch(path):
        raise ValueError("a backend factory is written package.module:function")
    module, name = path.split(":")
    return module, name


def load_factory(path: str) -> Callable[..., NerBackend]:
    """The backend factory at `path` ("package.module:function"). Only our own code calls this:
    the path never comes from a request."""
    module_name, name = split_factory(path)
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        module = None
    factory = getattr(module, name, None) if module is not None else None
    if not callable(factory):
        raise ValueError("the backend factory cannot be imported")
    return factory  # type: ignore[no-any-return]  # getattr gives Any; checked callable above
