"""Invariants with the NER layer (fake backend in process), with Hypothesis.

1. restore(mask(x)) == x with names, identifiers and placeholders mixed.
2. Propagation masks what the guard would find: after mask(), the guard never blocks the
   masker's own output because of a NER value, whatever the spelling of its other appearances.
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from antifaz import guard, mask, restore
from antifaz.detect.scan import Scanner
from tests.conftest import SENTINEL_DNI
from tests.nerfakes import ANA, CARMEN, JORDI, MARINA, fake_detector

SCANNER = Scanner(fake_detector()[0])

# Spellings the fake NER does not know (case, accents, spaces, glued): propagation must.
VARIANTS = [
    "carmen prueba lopez",
    "CARMEN PRUEBA LÓPEZ",
    "CarmenPruebaLópez",
    "jordi  inventat\npuig",
    "ana",
    "ANA",
    "semana",
    "submarina",
    "marina",
]
FILLER = ["Hola", "soy", ",", ".", "\n", "[[PERSON_1]]", "[[", "y", "ñandú", SENTINEL_DNI, "Ana"]
piece = st.sampled_from([CARMEN, JORDI, ANA, MARINA]) | st.sampled_from(VARIANTS + FILLER)
text = st.lists(piece, max_size=10).map(" ".join) | st.lists(piece, max_size=6).map("".join)
texts = st.lists(text, min_size=1, max_size=4)
# Without the DNI: two DNIs glued together ("12345678Z12345678Z") are a known detector limit
# (a DNI never touches digits) that the guard blocks; this test is about NER values.
name_piece = st.sampled_from([CARMEN, JORDI, ANA, MARINA]) | st.sampled_from(
    VARIANTS + [f for f in FILLER if f != SENTINEL_DNI]
)
name_texts = st.lists(
    st.lists(name_piece, max_size=10).map(" ".join) | st.lists(name_piece, max_size=6).map("".join),
    min_size=1,
    max_size=4,
)


@settings(max_examples=300, deadline=None)
@given(items=texts)
def test_restore_of_mask_is_identity_with_the_ner(items: list[str]) -> None:
    result = mask(items, detector=SCANNER)
    assert [restore(t, result.vault) for t in result.texts] == items


@settings(max_examples=300, deadline=None)
@given(items=name_texts)
def test_the_guard_never_blocks_what_the_masker_produced_with_the_ner(items: list[str]) -> None:
    result = mask(items, detector=SCANNER)
    for masked in result.texts:
        guard.check(masked, result.vault)
