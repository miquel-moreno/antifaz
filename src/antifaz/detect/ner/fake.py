"""A dictionary "NER" for tests and demos: finds the configured names, with word boundaries.

It can never be chosen by the settings: there is no variable for the backend, the gateway
always uses `setup.GLINER_FACTORY`, and only code (the tests) can pass another factory path
(`antifaz.detect.ner.fake:create`). With `triggers`, a few magic words make it misbehave so the
pool can be tested: FAKE_SLEEP (hangs), FAKE_CRASH (the process dies), FAKE_RAISE (an exception
holding the text), FAKE_MALFORMED (bad output) and FAKE_PRINT (writes the text to stdout, stderr
and the log).
"""

import logging
import os
import sys
import time
from collections.abc import Mapping


def _boundary(text: str, index: int) -> bool:
    return not 0 <= index < len(text) or not text[index].isalnum()


class FakeBackend:
    def __init__(
        self,
        names: Mapping[str, str],
        *,
        triggers: bool = False,
        score: float = 0.9,
        sleep_seconds: float = 30.0,
    ) -> None:
        self._names = dict(names)  # value -> label
        self._triggers = triggers
        self._score = score
        self._sleep_seconds = sleep_seconds

    def _misbehave(self, text: str) -> list[list[object]] | None:
        if "FAKE_SLEEP" in text:
            time.sleep(self._sleep_seconds)
        if "FAKE_CRASH" in text:
            os._exit(3)
        if "FAKE_RAISE" in text:
            raise RuntimeError(f"fake backend failed on: {text}")
        if "FAKE_PRINT" in text:
            print(text)
            print(text, file=sys.stderr)
            logging.getLogger("antifaz").error("fake backend saw: %s", text)
        if "FAKE_MALFORMED" in text:
            return [["bad"]]
        return None

    def _find(self, text: str, labels: list[str], threshold: float) -> list[list[object]]:
        found: list[list[object]] = []
        if self._score < threshold:
            return found
        for value, label in self._names.items():
            if label not in labels or not value:
                continue
            start = text.find(value)
            while start != -1:
                end = start + len(value)
                if _boundary(text, start - 1) and _boundary(text, end):
                    found.append([start, end, label, self._score])
                start = text.find(value, start + 1)
        return sorted(found, key=lambda entity: (entity[0], entity[1]))

    def predict(
        self, texts: list[str], labels: list[str], threshold: float
    ) -> list[list[list[object]]]:
        out = []
        for text in texts:
            odd = self._misbehave(text) if self._triggers else None
            out.append(odd if odd is not None else self._find(text, labels, threshold))
        return out


def create(
    names: Mapping[str, str],
    triggers: bool = False,
    score: float = 0.9,
    sleep_seconds: float = 30.0,
    model_dir: str | None = None,
    load_delay_file: str | None = None,
) -> FakeBackend:
    """Factory used by the worker processes (and by tests in the same process). `model_dir`
    is what the real backend gets from the settings; the fake does not need it. If the file
    `load_delay_file` exists, loading waits the seconds written in it (a slow model load)."""
    if load_delay_file and os.path.exists(load_delay_file):
        with open(load_delay_file, encoding="utf-8") as file:
            time.sleep(float(file.read()))
    return FakeBackend(names, triggers=triggers, score=score, sleep_seconds=sleep_seconds)
