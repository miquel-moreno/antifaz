"""Overlap resolution: validator beats pattern, then longer, then earlier, then type order."""

from itertools import permutations

from antifaz.detect.overlaps import resolve
from antifaz.detect.types import Confidence, EntityType, Layer, Span

V, P = Layer.VALIDATOR, Layer.PATTERN


def span(start: int, end: int, type_: EntityType, layer: Layer = V) -> Span:
    confidence = Confidence.HIGH if layer is V else Confidence.MEDIUM
    return Span(start, end, type_, layer, confidence)


def test_no_spans_gives_no_spans() -> None:
    assert resolve([]) == []


def test_validator_beats_a_longer_partly_overlapping_pattern() -> None:
    validated = span(5, 14, EntityType.ES_DNI, V)
    pattern = span(10, 30, EntityType.CREDIT_CARD, P)
    assert resolve([pattern, validated]) == [validated]


def test_a_span_that_contains_another_wins_whatever_its_layer() -> None:
    # ADR-0010: "juan.12345678Z@example.com" is masked whole, not only the DNI inside it.
    email = span(0, 26, EntityType.EMAIL, P)
    dni = span(5, 14, EntityType.ES_DNI, V)
    assert resolve([dni, email]) == [email]


def test_longer_span_wins_between_two_validators() -> None:
    iban = span(0, 29, EntityType.IBAN)
    ccc = span(5, 29, EntityType.ES_CCC)
    assert resolve([ccc, iban]) == [iban]


def test_earlier_start_wins_between_equal_length_spans_of_the_same_layer() -> None:
    first = span(0, 10, EntityType.ES_NSS)
    second = span(5, 15, EntityType.ES_NSS)
    assert resolve([second, first]) == [first]


def test_exact_tie_is_decided_by_entity_type_definition_order() -> None:
    # ES_NSS is defined before ES_CCC in EntityType, so it wins an exact tie.
    nss = span(0, 12, EntityType.ES_NSS)
    ccc = span(0, 12, EntityType.ES_CCC)
    assert resolve([ccc, nss]) == [nss]
    assert resolve([nss, ccc]) == [nss]


def test_touching_spans_do_not_overlap_and_are_both_kept() -> None:
    left = span(0, 9, EntityType.ES_DNI)
    right = span(9, 18, EntityType.ES_NIE)
    assert resolve([right, left]) == [left, right]


def test_a_discarded_span_does_not_block_later_spans() -> None:
    kept = span(0, 10, EntityType.ES_DNI, V)
    dropped = span(8, 30, EntityType.CREDIT_CARD, P)  # partial overlap: the validator wins
    also_kept = span(25, 35, EntityType.CREDIT_CARD, P)
    assert resolve([dropped, also_kept, kept]) == [kept, also_kept]


def test_output_is_sorted_by_start() -> None:
    spans = [
        span(40, 49, EntityType.ES_DNI),
        span(0, 9, EntityType.ES_NIE),
        span(20, 44, EntityType.IBAN),  # overlaps the DNI and is longer: wins
    ]
    result = resolve(spans)
    assert result == [spans[1], spans[2]]
    assert [s.start for s in result] == sorted(s.start for s in result)


def test_result_does_not_depend_on_input_order() -> None:
    spans = [
        span(0, 9, EntityType.ES_DNI, V),
        span(0, 9, EntityType.ES_NIF, V),
        span(4, 20, EntityType.CREDIT_CARD, P),
        span(9, 18, EntityType.ES_CIF, V),
        span(15, 30, EntityType.IBAN, P),
    ]
    results = {tuple(resolve(p)) for p in permutations(spans)}
    assert len(results) == 1


def test_resolve_accepts_any_iterable() -> None:
    spans = [span(0, 9, EntityType.ES_DNI)]
    assert resolve(s for s in spans) == spans


def test_if_a_container_loses_the_span_inside_it_competes_again() -> None:
    # Found by Hypothesis: A contains B, C (validator) beats A on a partial overlap. B must
    # not stay discarded, or its text would be left uncovered.
    container = span(0, 20, EntityType.EMAIL, P)
    inside = span(2, 8, EntityType.ES_NIE, V)
    winner = span(15, 30, EntityType.IBAN, V)
    assert resolve([container, inside, winner]) == [inside, winner]
