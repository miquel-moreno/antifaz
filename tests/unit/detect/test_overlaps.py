"""Overlap resolution: validator beats pattern beats NER, then longer, then earlier, then type
order. A partial-overlap loser is trimmed to the parts nobody else covers (ADR-0016)."""

from itertools import permutations

from antifaz.detect.overlaps import resolve
from antifaz.detect.types import Confidence, EntityType, Layer, Span

V, P, N = Layer.VALIDATOR, Layer.PATTERN, Layer.NER


def span(start: int, end: int, type_: EntityType, layer: Layer = V) -> Span:
    confidence = Confidence.HIGH if layer is V else Confidence.MEDIUM
    return Span(start, end, type_, layer, confidence)


def test_no_spans_gives_no_spans() -> None:
    assert resolve([]) == []


def test_validator_beats_a_longer_partly_overlapping_pattern_which_is_trimmed() -> None:
    validated = span(5, 14, EntityType.ES_DNI, V)
    pattern = span(10, 30, EntityType.CREDIT_CARD, P)
    assert resolve([pattern, validated]) == [validated, span(14, 30, EntityType.CREDIT_CARD, P)]


def test_a_span_that_contains_another_wins_whatever_its_layer() -> None:
    # ADR-0010: "juan.12345678Z@example.com" is masked whole, not only the DNI inside it.
    email = span(0, 26, EntityType.EMAIL, P)
    dni = span(5, 14, EntityType.ES_DNI, V)
    assert resolve([dni, email]) == [email]


def test_a_ner_span_never_swallows_a_validator_or_pattern_span() -> None:
    # "Carmen 12345678Z" seen as one name: the DNI stays a DNI (so a policy that allows names
    # still masks it) and the name is trimmed around it.
    person = span(0, 30, EntityType.PERSON, N)
    dni = span(10, 19, EntityType.ES_DNI, V)
    email = span(20, 28, EntityType.EMAIL, P)
    assert resolve([dni, person, email]) == [
        span(0, 10, EntityType.PERSON, N),
        dni,
        span(19, 20, EntityType.PERSON, N),
        email,
        span(28, 30, EntityType.PERSON, N),
    ]


def test_a_ner_span_containing_another_ner_span_wins() -> None:
    outer = span(0, 20, EntityType.PERSON, N)
    inner = span(5, 10, EntityType.ADDRESS, N)
    assert resolve([inner, outer]) == [outer]


def test_ner_has_the_lowest_priority_in_a_partial_overlap() -> None:
    phone = span(10, 21, EntityType.PHONE, P)
    person = span(0, 14, EntityType.PERSON, N)
    assert resolve([person, phone]) == [span(0, 10, EntityType.PERSON, N), phone]


def test_longer_span_wins_between_two_validators() -> None:
    iban = span(0, 29, EntityType.IBAN)
    ccc = span(5, 29, EntityType.ES_CCC)
    assert resolve([ccc, iban]) == [iban]


def test_earlier_start_wins_between_equal_length_spans_and_the_other_is_trimmed() -> None:
    first = span(0, 10, EntityType.ES_NSS)
    second = span(5, 15, EntityType.ES_NSS)
    assert resolve([second, first]) == [first, span(10, 15, EntityType.ES_NSS)]


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


def test_a_trimmed_span_keeps_competing_with_later_spans() -> None:
    kept = span(0, 10, EntityType.ES_DNI, V)
    trimmed = span(8, 30, EntityType.CREDIT_CARD, P)  # partial overlap: the validator wins
    later = span(25, 35, EntityType.CREDIT_CARD, P)  # shorter: loses to the trimmed card
    assert resolve([trimmed, later, kept]) == [
        kept,
        span(10, 30, EntityType.CREDIT_CARD, P),
        span(30, 35, EntityType.CREDIT_CARD, P),
    ]


def test_a_loser_covered_on_both_sides_keeps_the_gap_in_the_middle() -> None:
    left = span(0, 10, EntityType.ES_DNI)
    right = span(20, 30, EntityType.ES_NIE)
    middle = span(5, 25, EntityType.PERSON, N)
    assert resolve([middle, left, right]) == [left, span(10, 20, EntityType.PERSON, N), right]


def test_a_loser_fully_covered_by_winners_is_dropped() -> None:
    left = span(0, 10, EntityType.ES_DNI)
    right = span(10, 20, EntityType.ES_NIE)
    covered = span(5, 15, EntityType.PERSON, N)
    assert resolve([covered, left, right]) == [left, right]


def test_trimmed_pieces_without_letters_or_digits_are_dropped_when_the_text_is_given() -> None:
    text = "12345678Z - resto"
    dni = span(0, 9, EntityType.ES_DNI)
    noisy = span(5, 12, EntityType.PERSON, N)  # "5678Z -": its free part " -" is noise
    assert resolve([dni, noisy], text) == [dni]
    assert resolve([dni, noisy]) == [dni, span(9, 12, EntityType.PERSON, N)]


def test_output_is_sorted_by_start() -> None:
    spans = [
        span(40, 49, EntityType.ES_DNI),
        span(0, 9, EntityType.ES_NIE),
        span(20, 44, EntityType.IBAN),  # overlaps the DNI and is longer: wins
    ]
    result = resolve(spans)
    assert result == [spans[1], spans[2], span(44, 49, EntityType.ES_DNI)]
    assert [s.start for s in result] == sorted(s.start for s in result)


def test_result_does_not_depend_on_input_order() -> None:
    spans = [
        span(0, 9, EntityType.ES_DNI, V),
        span(0, 9, EntityType.ES_NIF, V),
        span(4, 20, EntityType.CREDIT_CARD, P),
        span(9, 18, EntityType.ES_CIF, V),
        span(15, 30, EntityType.IBAN, P),
        span(2, 26, EntityType.PERSON, N),
    ]
    results = {tuple(resolve(p)) for p in permutations(spans)}
    assert len(results) == 1


def test_resolve_accepts_any_iterable() -> None:
    spans = [span(0, 9, EntityType.ES_DNI)]
    assert resolve(s for s in spans) == spans


def test_if_a_container_loses_part_of_it_the_rest_stays_masked() -> None:
    # Found by Hypothesis (ADR-0010): A contains B, C (validator) beats A on a partial
    # overlap. Since ADR-0016 A is trimmed instead of dropped, so B stays covered inside it.
    container = span(0, 20, EntityType.EMAIL, P)
    inside = span(2, 8, EntityType.ES_NIE, V)
    winner = span(15, 30, EntityType.IBAN, V)
    assert resolve([container, inside, winner]) == [span(0, 15, EntityType.EMAIL, P), winner]
