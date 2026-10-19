"""MaskResult.found_spans (issue 53, PR 11d-a): where each value was found, never the value.

«Prueba un texto» shows which parts of the text were found and what was done with each, so
mask() reports, per text, the spans it worked with: start and end (code points of the input),
the entity type, whether it was hidden or allowed and, if hidden, its marker token. There is no
field for the value: the structure, its repr and the MaskResult repr hold none.

The spans may only be read by mask/ itself and by the panel's Try module (web/try_text.py): a
static check keeps them out of the audit (evidence) code, the Status view, the proxy and logs.
"""

import ast
import dataclasses
from pathlib import Path

import pytest

import antifaz
from antifaz import EntityType, mask, restore
from antifaz.detect.types import Confidence, Layer, Span
from antifaz.mask import FoundSpan
from tests.conftest import SENTINEL_DNI

CIF = "B12345674"
EMAIL = "ana@example.com"
SRC = Path(antifaz.__file__).parent
# The only modules that may read MaskResult.found_spans.
READERS = {"mask/__init__.py", "web/try_text.py"}


def test_found_spans_say_where_each_value_is_and_what_was_done() -> None:
    text = f"DNI {SENTINEL_DNI}, CIF {CIF}, mail {EMAIL}, again {SENTINEL_DNI}"

    result = mask(text)

    (spans,) = result.found_spans
    assert [text[s.start : s.end] for s in spans] == [SENTINEL_DNI, CIF, EMAIL, SENTINEL_DNI]
    assert [s.type for s in spans] == [
        EntityType.ES_DNI,
        EntityType.ES_CIF,
        EntityType.EMAIL,
        EntityType.ES_DNI,
    ]
    assert [s.hidden for s in spans] == [True, False, True, True]
    assert [s.token for s in spans] == ["ES_DNI_1", None, "EMAIL_1", "ES_DNI_1"]


def test_one_tuple_of_spans_per_text_in_input_order() -> None:
    result = mask(["nada", f"x {SENTINEL_DNI}", EMAIL])

    assert [len(spans) for spans in result.found_spans] == [0, 1, 1]
    assert result.found_spans[1][0].token == "ES_DNI_1"
    assert result.found_spans[2][0] == FoundSpan(0, len(EMAIL), EntityType.EMAIL, True, "EMAIL_1")


def test_every_hidden_token_is_one_the_vault_restores() -> None:
    text = f"{SENTINEL_DNI} {EMAIL} {CIF}"
    result = mask(text)

    for span in result.found_spans[0]:
        if span.hidden:
            assert restore(f"[[{span.token}]]", result.vault) == text[span.start : span.end]
        else:
            assert span.token is None


def test_the_structure_has_no_field_for_a_value() -> None:
    fields = {field.name for field in dataclasses.fields(FoundSpan)}

    assert fields == {"start", "end", "type", "hidden", "token"}
    assert FoundSpan.__slots__ == ("start", "end", "type", "hidden", "token")


def test_no_value_in_the_repr_of_the_spans_or_the_result() -> None:
    text = f"{SENTINEL_DNI} {EMAIL} {CIF}"
    result = mask(text)

    for value in (SENTINEL_DNI, EMAIL, CIF):
        assert value not in repr(result.found_spans)
        assert value not in str(result.found_spans)
    for value in (SENTINEL_DNI, EMAIL):  # the allowed CIF is in the masked text by design
        assert value not in repr(result)


def test_spans_are_frozen() -> None:
    span = mask(SENTINEL_DNI).found_spans[0][0]

    with pytest.raises(dataclasses.FrozenInstanceError):
        span.start = 1  # type: ignore[misc]


def test_a_failed_detection_reports_nothing() -> None:
    def broken(_: str) -> list[Span]:
        raise RuntimeError(SENTINEL_DNI)

    with pytest.raises(antifaz.DetectorFailed):
        mask(SENTINEL_DNI, detector=broken)


def test_spans_follow_a_custom_detector_and_the_policy() -> None:
    def detector(text: str) -> list[Span]:
        return [Span(0, 3, EntityType.PERSON, Layer.PATTERN, Confidence.HIGH)]

    result = mask("Ana viene", detector=detector)

    assert result.found_spans == ((FoundSpan(0, 3, EntityType.PERSON, True, "PERSON_1"),),)
    assert result.texts == ("[[PERSON_1]] viene",)


def _readers() -> set[str]:
    readers = set()
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        names |= {
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        }
        if "found_spans" in names:
            readers.add(path.relative_to(SRC).as_posix())
    return readers


def test_only_the_masker_and_the_try_module_touch_the_spans() -> None:
    readers = _readers()

    assert "mask/__init__.py" in readers  # not an empty pass
    assert readers <= READERS, readers - READERS
    for forbidden in ("audit/", "web/status", "api/", "logging.py"):
        assert not any(reader.startswith(forbidden) for reader in readers), forbidden


# --- review fixes -----------------------------------------------------------------------------


def test_the_result_repr_leaves_the_spans_out() -> None:
    result = mask(f"{SENTINEL_DNI} {EMAIL}")

    assert "found_spans" not in repr(result)
    assert "FoundSpan" not in repr(result)


def _calls(tree: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            names.add(func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", ""))
    return names


def test_no_module_dumps_a_dataclass_wholesale() -> None:
    """dataclasses.asdict/astuple would copy a MaskResult (spans and vault) into plain data that
    a log line or a JSON answer could carry: no module of the package calls them."""
    offenders = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if _calls(tree) & {"asdict", "astuple"}:
            offenders.append(path.relative_to(SRC).as_posix())

    assert offenders == []
    assert _calls(ast.parse("dataclasses.asdict(r)\nastuple(r)")) >= {"asdict", "astuple"}


def test_a_propagated_ner_value_is_reported_at_every_appearance() -> None:
    """The NER saw the name once; propagate() hides its other appearance (ADR-0016), and the
    spans say so with the same marker."""
    name = "Zoraya"
    text = f"{name} llama. Luego {name} vuelve."

    def ner_once(value: str) -> list[Span]:
        start = value.find(name)
        return [Span(start, start + len(name), EntityType.PERSON, Layer.NER, Confidence.MEDIUM)]

    result = mask(text, detector=ner_once)

    (spans,) = result.found_spans
    assert [text[s.start : s.end] for s in spans] == [name, name]
    assert [s.token for s in spans] == ["PERSON_1", "PERSON_1"]
    assert all(s.hidden for s in spans)
    assert name not in result.text
