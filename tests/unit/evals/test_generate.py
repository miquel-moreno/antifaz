"""Synthetic generator: determinism, span invariants, valid identifiers, traps, JSONL.

Every value is synthetic and built by the generator itself.
"""

import json
import random
import re
from collections import Counter
from collections.abc import Callable

import pytest
from evals.datasets.meddocan import Document
from evals.generate import (
    DATASET,
    KINDS,
    from_jsonl,
    generate,
    make_card,
    make_ccc,
    make_cif,
    make_dni,
    make_email,
    make_iban_es,
    make_nie,
    make_nif_klm,
    make_nss,
    make_phone,
    to_jsonl,
)
from stdnum import iban as std_iban
from stdnum.es import ccc as std_ccc
from stdnum.es import cif as std_cif
from stdnum.es import dni as std_dni
from stdnum.es import nie as std_nie
from stdnum.es import nif as std_nif

from antifaz.detect.patterns.personal import is_card_prefix
from antifaz.detect.types import EntityType
from antifaz.detect.validators import ccc, cif, dni, iban, luhn, nie, nif_klm, nss
from antifaz.detect.validators._ascii import compact

SMALL = {kind: 2 for kind in KINDS}
LABELS = {t.value for t in EntityType}
EMAIL_RE = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")
ALLOWED_EMAIL_DOMAIN = re.compile(r"(?:.+\.)?example\.(?:com|org|net)", re.IGNORECASE)
# 8 digits + letter, optionally with dots and a separator before the letter: DNI-shaped.
DNI_SHAPED = re.compile(r"(?<![\w.-])(\d{2}\.?\d{3}\.?\d{3})[- ]?([A-Za-z])(?!\w)")
BUILDERS: tuple[Callable[[random.Random], str], ...] = (
    make_dni,
    make_nie,
    make_nif_klm,
    make_cif,
    make_nss,
    make_ccc,
    make_iban_es,
    make_card,
    make_phone,
    make_email,
)


def check_invariants(documents: list[Document]) -> None:
    """Span invariants shared with the property test."""
    for doc in documents:
        previous_end = 0
        for ann in doc.annotations:
            assert 0 <= ann.start < ann.end <= len(doc.text), doc.name
            assert doc.text[ann.start : ann.end].strip(), doc.name
            assert ann.start >= previous_end, f"overlap or unsorted in {doc.name}"
            assert ann.label in LABELS, ann.label
            previous_end = ann.end


def _card_is_valid(value: str) -> bool:
    return luhn.is_valid(value) and is_card_prefix(compact(value))


VALIDATORS: dict[str, Callable[[str], bool]] = {
    EntityType.ES_DNI: dni.is_valid,
    EntityType.ES_NIE: nie.is_valid,
    EntityType.ES_NIF: nif_klm.is_valid,
    EntityType.ES_CIF: cif.is_valid,
    EntityType.ES_NSS: nss.is_valid,
    EntityType.ES_CCC: ccc.is_valid,
    EntityType.IBAN: iban.is_valid,
    EntityType.CREDIT_CARD: _card_is_valid,
}


@pytest.fixture(scope="module")
def small() -> list[Document]:
    return generate(seed=7, counts=SMALL)


# --- Determinism --------------------------------------------------------------------------


def test_same_seed_produces_identical_documents(small: list[Document]) -> None:
    assert generate(seed=7, counts=SMALL) == small


def test_different_seed_produces_different_documents(small: list[Document]) -> None:
    assert generate(seed=8, counts=SMALL) != small


# --- Span invariants ----------------------------------------------------------------------


def test_annotations_are_in_bounds_sorted_non_empty_and_non_overlapping(
    small: list[Document],
) -> None:
    check_invariants(small)


def test_every_label_is_an_antifaz_entity_type(small: list[Document]) -> None:
    assert {a.label for d in small for a in d.annotations} <= LABELS


def test_identifier_annotations_pass_our_validators(small: list[Document]) -> None:
    for doc in small:
        for ann in doc.annotations:
            check = VALIDATORS.get(ann.label)
            if check is not None:
                assert check(doc.text[ann.start : ann.end]), (doc.name, ann.label)


def test_non_trap_documents_have_annotations(small: list[Document]) -> None:
    assert all(d.annotations for d in small if not d.name.startswith("trap-"))


# --- Counts and names ---------------------------------------------------------------------


def test_names_follow_kind_and_three_digit_number_and_are_unique(
    small: list[Document],
) -> None:
    names = [d.name for d in small]
    assert len(names) == len(set(names))
    for name in names:
        kind, _, number = name.rpartition("-")
        assert kind in KINDS
        assert re.fullmatch(r"\d{3}", number)


def test_each_kind_appears_with_the_requested_count() -> None:
    counts = {"email": 3, "trap": 1, "catalan": 2}
    docs = generate(seed=1, counts=counts)
    assert Counter(d.name.rpartition("-")[0] for d in docs) == counts


def test_default_kinds_add_up_to_600() -> None:
    assert sum(KINDS.values()) == 600
    assert set(KINDS) == {
        "email",
        "ticket",
        "payslip",
        "contract",
        "chat",
        "csv",
        "code",
        "catalan",
        "trap",
    }


def test_default_generation_has_600_documents_with_the_kind_counts() -> None:
    docs = generate()
    assert len(docs) == 600
    assert Counter(d.name.rpartition("-")[0] for d in docs) == dict(KINDS)
    check_invariants(docs)


# --- Builders -----------------------------------------------------------------------------


def _rng(seed: int) -> random.Random:
    return random.Random(seed)  # noqa: S311 - reproducible synthetic data, not security


def _values(builder: Callable[[random.Random], str]) -> list[str]:
    return [builder(_rng(seed)) for seed in range(300)]


def test_builders_are_deterministic_for_a_seed() -> None:
    for builder in BUILDERS:
        assert builder(_rng(3)) == builder(_rng(3)), builder.__name__


def test_make_dni_is_valid_for_stdnum_and_ours() -> None:
    for v in _values(make_dni):
        assert std_dni.is_valid(compact(v)) and dni.is_valid(v), v


def test_make_nie_is_valid_for_stdnum_and_ours() -> None:
    for v in _values(make_nie):
        assert std_nie.is_valid(compact(v)) and nie.is_valid(v), v


def test_make_nif_klm_is_valid_for_stdnum_and_ours() -> None:
    for v in _values(make_nif_klm):
        assert compact(v)[0] in "KLM", v
        assert std_nif.is_valid(compact(v)) and nif_klm.is_valid(v), v


def test_make_cif_is_valid_for_stdnum_and_ours() -> None:
    for v in _values(make_cif):
        assert std_cif.is_valid(compact(v)) and cif.is_valid(v), v


def test_make_ccc_is_valid_for_stdnum_and_ours() -> None:
    for v in _values(make_ccc):
        assert std_ccc.is_valid(compact(v)) and ccc.is_valid(v), v


def test_make_iban_es_is_valid_for_stdnum_and_ours() -> None:
    for v in _values(make_iban_es):
        assert compact(v).startswith("ES"), v
        assert std_iban.is_valid(compact(v)) and iban.is_valid(v), v


def test_make_nss_is_valid_for_our_validator() -> None:
    for v in _values(make_nss):
        assert nss.is_valid(v), v


def test_make_card_passes_luhn_and_has_a_known_prefix() -> None:
    for v in _values(make_card):
        assert _card_is_valid(v), v


def test_builders_vary_and_sometimes_use_separators() -> None:
    for builder in (make_dni, make_iban_es, make_card, make_phone):
        values = _values(builder)
        assert any(compact(v) != v for v in values), builder.__name__
        assert any(compact(v) == v for v in values), builder.__name__
        assert len(set(values)) > len(values) // 2, builder.__name__


def test_make_phone_is_a_spanish_number() -> None:
    for v in _values(make_phone):
        digits = re.sub(r"\D", "", v)
        if v.startswith("0034"):
            digits = digits[4:]
        elif v.startswith("+34"):
            digits = digits[2:]
        assert len(digits) == 9 and digits[0] in "6789", v


def test_make_email_uses_reserved_example_domains() -> None:
    for v in _values(make_email):
        match = EMAIL_RE.fullmatch(v)
        assert match and ALLOWED_EMAIL_DOMAIN.fullmatch(match.group(1)), v


# --- Traps --------------------------------------------------------------------------------


def test_trap_documents_contain_a_wrong_letter_dni_that_is_not_annotated() -> None:
    found = 0
    for doc in generate(seed=7, counts={"trap": 10}):
        spans = [(a.start, a.end) for a in doc.annotations]
        for m in DNI_SHAPED.finditer(doc.text):
            if not std_dni.is_valid(m.group(1).replace(".", "") + m.group(2)):
                found += 1
                assert not any(s < m.end() and m.start() < e for s, e in spans), doc.name
    assert found > 0


def test_dni_annotations_are_never_invalid_for_stdnum(small: list[Document]) -> None:
    for doc in small:
        for ann in doc.annotations:
            if ann.label == EntityType.ES_DNI:
                assert std_dni.is_valid(compact(doc.text[ann.start : ann.end])), doc.name


def test_trap_annotations_hold_only_valid_identifiers() -> None:
    for doc in generate(seed=7, counts={"trap": 10}):
        for ann in doc.annotations:
            check = VALIDATORS.get(ann.label)
            if check is not None:
                assert check(doc.text[ann.start : ann.end]), doc.name


# --- Privacy ------------------------------------------------------------------------------


def test_every_email_in_the_texts_uses_a_reserved_example_domain(
    small: list[Document],
) -> None:
    for doc in small:
        for m in EMAIL_RE.finditer(doc.text):
            assert ALLOWED_EMAIL_DOMAIN.fullmatch(m.group(1)), doc.name


# --- JSONL --------------------------------------------------------------------------------


def test_jsonl_round_trip(small: list[Document]) -> None:
    assert from_jsonl(to_jsonl(small)) == small


def test_jsonl_is_one_sorted_key_line_per_document_without_ascii_escapes() -> None:
    docs = [Document(name="catalan-000", text="Façana d'en Àngel", annotations=())]
    out = to_jsonl(docs)
    lines = out.splitlines()
    assert len(lines) == 1
    assert "Façana" in out and "\\u" not in out
    record = json.loads(lines[0])
    assert list(record) == sorted(record)


def test_committed_dataset_is_reproducible() -> None:
    if not DATASET.exists():
        pytest.skip("synthetic-v1.jsonl not generated yet")
    assert DATASET.read_text(encoding="utf-8") == to_jsonl(generate())
