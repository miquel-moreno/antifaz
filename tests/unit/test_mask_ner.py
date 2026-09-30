"""mask() with the NER: one batch per request and propagation of NER values (ADR-0016).

Every value the NER found (and the policy hides) is also masked wherever else it appears in the
request, with the guard's rule: long values (6+ letters and digits) without word boundaries,
short ones with boundaries. All names are invented.
"""

from collections.abc import Sequence
from types import MappingProxyType

import pytest

from antifaz import DetectorFailed, guard, mask, restore
from antifaz.detect.scan import Scanner
from antifaz.detect.types import Confidence, EntityType, Layer, Span
from antifaz.policy import Action, Policy
from tests.conftest import SENTINEL_DNI
from tests.nerfakes import ANA, CARMEN, MARINA, fake_detector


def only_first(value: str, entity: EntityType = EntityType.PERSON, layer: Layer = Layer.NER):
    """A detector that finds `value` only the first time it is called with it (a NER that
    misses the other appearances)."""
    seen: list[str] = []

    def detector(text: str) -> Sequence[Span]:
        start = text.find(value)
        if start == -1 or seen:
            return []
        seen.append(text)
        return [Span(start, start + len(value), entity, layer, Confidence.MEDIUM)]

    return detector


def test_a_ner_value_is_masked_in_every_text_of_the_request() -> None:
    texts = [f"Hola, soy {CARMEN}.", f"{CARMEN} otra vez", f"adiós {CARMEN}"]
    result = mask(texts, detector=only_first(CARMEN))
    assert result.texts == (
        "Hola, soy [[PERSON_1]].",
        "[[PERSON_1]] otra vez",
        "adiós [[PERSON_1]]",
    )
    assert [restore(t, result.vault) for t in result.texts] == texts


def test_other_spellings_of_a_long_value_are_masked_too() -> None:
    texts = [CARMEN, "carmen prueba lopez", "CARMEN  PRUEBA-LÓPEZ!"]
    result = mask(texts, detector=only_first(CARMEN))
    assert result.texts == ("[[PERSON_1]]", "[[PERSON_2]]", "[[PERSON_3]]!")
    assert [restore(t, result.vault) for t in result.texts] == texts


def test_a_long_value_is_masked_inside_another_word_the_guard_rule() -> None:
    # Accepted false positive (ADR-0016): "Marina" in "submarina". Without it the guard blocks.
    result = mask([f"Soy {MARINA}", "un submarina amarillo"], detector=only_first(MARINA))
    assert result.texts == ("Soy [[PERSON_1]]", "un sub[[PERSON_2]] amarillo")


def test_a_short_value_needs_word_boundaries() -> None:
    texts = [f"Soy {ANA}", "la semana que viene", "ANA, ana y Ana."]
    result = mask(texts, detector=only_first(ANA))
    assert result.texts == (
        "Soy [[PERSON_1]]",
        "la semana que viene",
        "[[PERSON_2]], [[PERSON_3]] y [[PERSON_1]].",
    )


def test_values_the_policy_allows_are_not_propagated() -> None:
    policy = Policy(entities=MappingProxyType({EntityType.PERSON: Action.ALLOW}))
    texts = [f"Soy {CARMEN}", f"{CARMEN} otra vez"]
    assert mask(texts, policy, detector=only_first(CARMEN)).texts == tuple(texts)


def test_only_ner_values_are_propagated() -> None:
    # Patterns and validators already find every appearance: nothing to propagate.
    detector = only_first(SENTINEL_DNI, EntityType.ES_DNI, Layer.VALIDATOR)
    result = mask([SENTINEL_DNI, SENTINEL_DNI], detector=detector)
    assert result.texts == ("[[ES_DNI_1]]", SENTINEL_DNI)


def test_a_propagated_value_inside_an_email_leaves_the_email_whole() -> None:
    scanner = Scanner(fake_detector({"Carmen": "person"})[0])
    texts = ["Soy Carmen", "escribe a carmen.prueba@example.com"]
    result = mask(texts, detector=scanner)
    assert result.texts == ("Soy [[PERSON_1]]", "escribe a [[EMAIL_1]]")


def test_the_scanner_sends_all_texts_to_the_ner_in_one_batch() -> None:
    ner, predictor = fake_detector()
    result = mask([f"Soy {CARMEN}", "hola", f"DNI {SENTINEL_DNI}"], detector=Scanner(ner))
    assert result.texts == ("Soy [[PERSON_1]]", "hola", "DNI [[ES_DNI_1]]")
    assert predictor.calls == 1


def test_a_failing_batch_detector_blocks() -> None:
    class Broken(Scanner):
        def scan_many(self, texts: Sequence[str]) -> list[list[Span]]:
            raise RuntimeError(f"broken on {texts}")

    with pytest.raises(DetectorFailed) as error:
        mask([SENTINEL_DNI], detector=Broken())
    assert SENTINEL_DNI not in str(error.value)
    assert error.value.__context__ is None


def test_a_batch_detector_with_the_wrong_number_of_results_blocks() -> None:
    class Short(Scanner):
        def scan_many(self, texts: Sequence[str]) -> list[list[Span]]:
            return []

    with pytest.raises(DetectorFailed):
        mask(["uno", "dos"], detector=Short())


def test_the_guard_accepts_what_propagation_masked() -> None:
    texts = [f"Soy {CARMEN}", "carmen prueba lópez", f"{ANA} y la semana", "ana"]
    detector = only_first(CARMEN)
    result = mask(texts, detector=detector)
    for text in result.texts:
        guard.check(text, result.vault)


def test_a_short_value_glued_to_a_masked_one_is_masked_as_the_guard_would_see_it() -> None:
    # Found by Hypothesis: "[[PERSON_1]]Ana" has a boundary before "Ana" for the guard.
    scanner = Scanner(fake_detector()[0])
    texts = [f"{CARMEN} y {ANA}", f"{CARMEN}{ANA}", f"{CARMEN}{ANA}s"]
    result = mask(texts, detector=scanner)
    assert result.texts == (
        "[[PERSON_1]] y [[PERSON_2]]",
        "[[PERSON_1]][[PERSON_2]]",
        "[[PERSON_1]]Anas",
    )
    for text in result.texts:
        guard.check(text, result.vault)
