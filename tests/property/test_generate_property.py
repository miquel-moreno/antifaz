"""Generator invariants hold for any seed (small counts, all synthetic)."""

from evals.generate import KINDS, generate
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.unit.evals.test_generate import VALIDATORS, check_invariants

SMALL = {kind: 1 for kind in KINDS}
SEEDS = st.integers(min_value=0, max_value=2**32 - 1)


@settings(max_examples=100, deadline=None)
@given(seed=SEEDS)
def test_generated_documents_keep_span_invariants_for_any_seed(seed: int) -> None:
    check_invariants(generate(seed=seed, counts=SMALL))


@settings(max_examples=100, deadline=None)
@given(seed=SEEDS)
def test_identifier_annotations_are_valid_for_any_seed(seed: int) -> None:
    for doc in generate(seed=seed, counts=SMALL):
        for ann in doc.annotations:
            check = VALIDATORS.get(ann.label)
            if check is not None:
                assert check(doc.text[ann.start : ann.end]), (seed, doc.name, ann.label)
