"""The committed results (evals/results/*.json) never carry a value from the documents (ADR-0011).

Checked against every annotated value of the synthetic set (in the repository) and, where the
MEDDOCAN zip has been downloaded (make bench), against every annotated value of MEDDOCAN too.
"""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from evals.datasets import meddocan
from evals.datasets.meddocan import Document
from evals.generate import from_jsonl

ROOT = Path(__file__).resolve().parents[3]
RESULTS = sorted((ROOT / "evals" / "results").glob("*.json"))
SYNTHETIC = ROOT / "evals" / "datasets" / "synthetic-v1.jsonl"
# Shorter values ("12", "ES", "Ana") can appear by chance in numbers or labels.
MIN_LENGTH = 6


def _strings(node: object) -> Iterator[str]:
    """Every string in a JSON document, keys included."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from _strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from _strings(item)


def _values(documents: list[Document]) -> set[str]:
    return {
        d.text[a.start : a.end]
        for d in documents
        for a in d.annotations
        if len(d.text[a.start : a.end].strip()) >= MIN_LENGTH
    }


def _check(values: set[str]) -> None:
    assert RESULTS, "no committed results"
    for path in RESULTS:
        text = "\n".join(_strings(json.loads(path.read_text(encoding="utf-8"))))
        found = sorted(value for value in values if value in text)
        # Say where, never what: the value itself is not printed.
        assert not found, f"{path.name}: {len(found)} document values"


def test_committed_results_carry_no_synthetic_value() -> None:
    _check(_values(from_jsonl(SYNTHETIC.read_text(encoding="utf-8"))))


def test_committed_results_carry_no_meddocan_value() -> None:
    archive = meddocan.CACHE_DIR / "meddocan.zip"
    if not archive.exists():
        pytest.skip("MEDDOCAN not downloaded (make bench)")
    documents = meddocan.load_test(archive) + meddocan.load_dev(archive)
    _check(_values(documents))
