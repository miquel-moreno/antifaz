"""The pure pieces of «Prueba un texto» (issue 53, PR 11d-a, ADR-0018): web/try_text.py.

- parse_body(): exactly {"text": str, "lang": a loaded code}; anything else is TryBodyError,
  whose message never quotes the input. A text over MAX_TEXT_BYTES UTF-8 bytes is TextTooLongError.
- utf16_offsets() / utf16_length(): Python indexes code points, JavaScript indexes UTF-16 code
  units. The API answers in UTF-16, so the page can slice its own string. The table below checks,
  for emoji (astral, ZWJ sequences, flags), accents precomposed and decomposed, CRLF, RTL text
  and bidi controls, with the value glued to them or not, that the UTF-16 slice of the original
  text (simulated with UTF-16-LE bytes) is exactly the value detected.
- simulate_reply(tokens, lang): built ONLY from marker tokens; never the text or the vault.
- run_try(): the real pipeline (mask -> guard -> simulated reply -> restore), no AI called.
- The import closure of the module: no provider, no gateway route, no httpx, socket or ssl.
"""

import ast
import inspect
import json
import re
from typing import Any

import pytest

from antifaz import EntityType
from antifaz.detect.types import Confidence, Layer, Span
from antifaz.policy import DEFAULT_POLICY
from antifaz.web import try_text
from antifaz.web.try_text import (
    MAX_LISTED,
    MAX_TEXT_BYTES,
    TextTooLongError,
    TryBodyError,
    parse_body,
    run_try,
    simulate_reply,
    utf16_length,
    utf16_offsets,
)
from tests.conftest import SENTINEL_DNI
from tests.unit.test_web_never_reads_the_token import SRC, _loaded, _project_closure

LANGS = ("en", "es")
EMAIL = "ana@example.com"
IBAN = "ES9121000418450200051332"
CIF = "B12345674"
# Written with chr(): invisible or ambiguous characters never sit in the source.
ACUTE = chr(0x301)  # combining acute accent
RLO = chr(0x202E)  # right-to-left override
PDF = chr(0x202C)  # pop directional formatting
BOLD_A = chr(0x1D400)  # mathematical bold capital A (astral)
THUMB = chr(0x1F44D)
# man, woman, girl joined by ZERO WIDTH JOINERs: one emoji on screen, 8 UTF-16 units.
FAMILY = chr(0x200D).join([chr(0x1F468), chr(0x1F469), chr(0x1F467)])
FLAG = chr(0x1F1EA) + chr(0x1F1F8)  # regional indicators E S


def _body(**values: object) -> bytes:
    return json.dumps(values).encode()


# --- parse_body -------------------------------------------------------------------------------


def test_a_good_body() -> None:
    assert parse_body(_body(text=f"DNI {SENTINEL_DNI}", lang="es"), LANGS) == (
        f"DNI {SENTINEL_DNI}",
        "es",
    )
    assert parse_body(b'{"lang": "en", "text": ""}', LANGS) == ("", "en")


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"[]",
        b'"text"',
        b"null",
        b"{}",
        b'{"text": "x"}',
        b'{"lang": "en"}',
        b'{"text": "x", "lang": "en", "extra": 1}',
        b'{"text": "x", "text": "y", "lang": "en"}',  # repeated key
        b'{"text": "x", "lang": "en", "lang": "es"}',
        b'{"text": 1, "lang": "en"}',
        b'{"text": true, "lang": "en"}',
        b'{"text": null, "lang": "en"}',
        b'{"text": ["x"], "lang": "en"}',
        b'{"text": {"a": "b"}, "lang": "en"}',
        b'{"text": NaN, "lang": "en"}',
        b'{"text": "x", "lang": Infinity}',
        b'{"text": "x", "lang": -Infinity}',
        b'{"text": "x", "lang": "fr"}',  # not a loaded catalog
        b'{"text": "x", "lang": "EN"}',
        b'{"text": "x", "lang": 1}',
        b'{"text": "\\ud800", "lang": "en"}',  # lone surrogate
        b'{"text": "a\\udfffb", "lang": "en"}',
        b'{"text": "x", "lang": "\\ud800"}',
        b'\xef\xbb\xbf{"text": "x", "lang": "en"}',  # BOM
        b'{"text": "\xff", "lang": "en"}',  # not UTF-8
        b'{"text": "x", "lang": "en"',
        b'{"Text": "x", "lang": "en"}',
    ],
)
def test_a_bad_body_is_one_fixed_error(raw: bytes) -> None:
    with pytest.raises(TryBodyError) as caught:
        parse_body(raw, LANGS)

    assert str(caught.value) == TryBodyError.message
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_a_deeply_nested_body_is_a_bad_body() -> None:
    with pytest.raises(TryBodyError):
        parse_body(b"[" * 100_000, LANGS)


def test_the_error_never_quotes_the_input() -> None:
    raw = _body(text=SENTINEL_DNI, lang="xx")
    with pytest.raises(TryBodyError) as caught:
        parse_body(raw, LANGS)

    assert SENTINEL_DNI not in repr(caught.value)
    assert "xx" not in str(caught.value)


def test_the_text_size_is_counted_in_utf8_bytes() -> None:
    assert parse_body(_body(text="a" * MAX_TEXT_BYTES, lang="en"), LANGS)[0]
    assert parse_body(_body(text="é" * (MAX_TEXT_BYTES // 2), lang="en"), LANGS)[0]
    for text in ("a" * (MAX_TEXT_BYTES + 1), "é" * (MAX_TEXT_BYTES // 2 + 1), "👍" * 5121):
        with pytest.raises(TextTooLongError) as caught:
            parse_body(_body(text=text, lang="en"), LANGS)
        assert caught.value.__context__ is None


def test_a_too_large_text_with_a_bad_body_is_a_bad_body() -> None:
    raw = _body(text="a" * (MAX_TEXT_BYTES + 1), lang="fr")

    with pytest.raises(TryBodyError):
        parse_body(raw, LANGS)


# --- UTF-16 offsets ---------------------------------------------------------------------------


def _js_slice(text: str, start: int, end: int) -> str:
    """What `text.slice(start, end)` gives in JavaScript: UTF-16 code units."""
    units = text.encode("utf-16-le")
    return units[2 * start : 2 * end].decode("utf-16-le")


def test_utf16_length_counts_astral_characters_twice() -> None:
    assert utf16_length("") == 0
    assert utf16_length("abc") == 3
    assert utf16_length("é") == 1
    assert utf16_length(f"e{ACUTE}") == 2
    assert utf16_length("👍") == 2
    assert utf16_length(FAMILY) == 8
    assert utf16_length("🇪🇸") == 4
    assert utf16_length("\r\n") == 2


def test_utf16_offsets_of_some_positions() -> None:
    text = f"a{THUMB}b{BOLD_A}c"

    assert utf16_offsets(text, [0, 1, 2, 3, 4, 5]) == {0: 0, 1: 1, 2: 3, 3: 4, 4: 6, 5: 7}
    assert utf16_offsets(text, []) == {}
    assert utf16_offsets(text, [len(text)]) == {len(text): utf16_length(text)}


def test_utf16_offsets_refuse_a_position_outside_the_text() -> None:
    with pytest.raises(ValueError):
        utf16_offsets("abc", [4])
    with pytest.raises(ValueError):
        utf16_offsets("abc", [-1])


OFFSET_CASES = [
    (f"👍 {SENTINEL_DNI}", [SENTINEL_DNI]),
    (f"👍{SENTINEL_DNI}👍", [SENTINEL_DNI]),
    (f"{FAMILY} {SENTINEL_DNI} {FAMILY} {EMAIL}", [SENTINEL_DNI, EMAIL]),
    (f"José {SENTINEL_DNI}", [SENTINEL_DNI]),
    (f"Jose{ACUTE} {SENTINEL_DNI}", [SENTINEL_DNI]),
    (f"{SENTINEL_DNI}{ACUTE} x", [SENTINEL_DNI]),
    (f"línea\r\n{SENTINEL_DNI}\r\nfin", [SENTINEL_DNI]),
    (f"مرحبا {SENTINEL_DNI} שלום", [SENTINEL_DNI]),
    (f"{RLO}{SENTINEL_DNI}{PDF}", [SENTINEL_DNI]),
    (f"{EMAIL}👍", [EMAIL]),
    (f"{BOLD_A} {IBAN} {FLAG}", [IBAN]),
    (f"e{ACUTE}{ACUTE} 612345678 {THUMB}{SENTINEL_DNI}", ["612345678", SENTINEL_DNI]),
    (f"🇪🇸🇪🇸🇪🇸 {CIF} 👍 {SENTINEL_DNI}", [CIF, SENTINEL_DNI]),
]


@pytest.mark.parametrize(("text", "values"), OFFSET_CASES)
def test_the_utf16_slice_of_each_entity_is_exactly_the_value(text: str, values: list[str]) -> None:
    result = run_try(text, "en", detector=_scan(), policy=DEFAULT_POLICY)

    assert result["length_utf16"] == len(text.encode("utf-16-le")) // 2
    found = [_js_slice(text, e["start"], e["end"]) for e in result["entities"]]
    assert found == values


def _scan() -> Any:
    from antifaz.detect.scan import scan

    return scan


# --- simulate_reply ---------------------------------------------------------------------------

TOKEN = re.compile(r"\[\[([A-Z]+(?:_[A-Z]+)*_[0-9]+)\]\]")


def test_the_reply_names_every_marker_once_in_order() -> None:
    reply = simulate_reply(["ES_DNI_1", "EMAIL_1", "PERSON_2"], "en")

    assert TOKEN.findall(reply) == ["ES_DNI_1", "EMAIL_1", "PERSON_2"]


def test_without_markers_the_reply_says_nothing_was_hidden() -> None:
    for lang in LANGS:
        reply = simulate_reply([], lang)
        assert reply and "[[" not in reply


def test_a_long_list_is_capped() -> None:
    tokens = [f"EMAIL_{n}" for n in range(1, MAX_LISTED + 6)]

    reply = simulate_reply(tokens, "en")

    assert TOKEN.findall(reply) == tokens[:MAX_LISTED]
    assert "5 more" in reply
    assert "5 más" in simulate_reply(tokens, "es")


def test_the_reply_is_deterministic_and_depends_on_the_language() -> None:
    tokens = ["ES_DNI_1", "IBAN_1"]

    assert simulate_reply(tokens, "es") == simulate_reply(tokens, "es")
    assert simulate_reply(tokens, "es") != simulate_reply(tokens, "en")


def test_an_unknown_language_falls_back_to_english() -> None:
    assert simulate_reply(["ES_DNI_1"], "pt-BR") == simulate_reply(["ES_DNI_1"], "en")


@pytest.mark.parametrize("bad", [SENTINEL_DNI, "es_dni_1", "[[ES_DNI_1]]", "ES_DNI", "", "A_1 "])
def test_anything_but_a_token_is_refused(bad: str) -> None:
    with pytest.raises(ValueError) as caught:
        simulate_reply(["EMAIL_1", bad], "en")

    assert SENTINEL_DNI not in str(caught.value)


def test_simulate_reply_takes_only_the_tokens_and_the_language() -> None:
    assert list(inspect.signature(simulate_reply).parameters) == ["tokens", "lang"]


# --- run_try ----------------------------------------------------------------------------------


def _joined(segments: list[dict[str, Any]], key: str) -> str:
    return "".join(s["text"] if "text" in s else s[key] for s in segments)


def test_the_pipeline_masks_simulates_and_restores() -> None:
    text = f"Hola, soy {SENTINEL_DNI} ({EMAIL}), empresa {CIF}."

    result = run_try(text, "es", detector=_scan(), policy=DEFAULT_POLICY)

    assert result["outcome"] == "masked"
    assert result["blocked_reason"] is None
    assert result["policy_version"] == DEFAULT_POLICY.version
    assert [(e["type"], e["hidden"]) for e in result["entities"]] == [
        (EntityType.ES_DNI, True),
        (EntityType.EMAIL, True),
        (EntityType.ES_CIF, False),
    ]
    sent = _joined(result["sent"], "marker")
    assert sent == "Hola, soy [[ES_DNI_1]] ([[EMAIL_1]]), empresa B12345674."
    assert SENTINEL_DNI not in sent and EMAIL not in sent
    markers = [s for s in result["sent"] if "marker" in s]
    assert markers == [
        {"marker": "[[ES_DNI_1]]", "type": EntityType.ES_DNI},
        {"marker": "[[EMAIL_1]]", "type": EntityType.EMAIL},
    ]
    values = [(s["type"], s["value"]) for s in result["reply"] if "marker" in s]
    assert values == [(EntityType.ES_DNI, SENTINEL_DNI), (EntityType.EMAIL, EMAIL)]
    assert result["counts"] == [
        {"type": EntityType.ES_DNI, "found": 1, "hidden": 1},
        {"type": EntityType.ES_CIF, "found": 1, "hidden": 0},
        {"type": EntityType.EMAIL, "found": 1, "hidden": 1},
    ]
    assert isinstance(result["elapsed_ms"], int) and result["elapsed_ms"] >= 0
    assert "text" not in result  # the original text is never echoed


def test_the_joined_reply_is_restore_of_the_whole_simulated_reply() -> None:
    text = f"{SENTINEL_DNI} y {SENTINEL_DNI} y {EMAIL}"

    result = run_try(text, "en", detector=_scan(), policy=DEFAULT_POLICY)

    simulated = simulate_reply(["ES_DNI_1", "EMAIL_1"], "en")
    restored = simulated.replace("[[ES_DNI_1]]", SENTINEL_DNI).replace("[[EMAIL_1]]", EMAIL)
    assert _joined(result["reply"], "value") == restored
    assert _joined(result["reply"], "marker") == simulated


def test_repeated_values_have_one_marker_and_counts_by_type() -> None:
    text = f"{SENTINEL_DNI} {SENTINEL_DNI} 87654321X"

    result = run_try(text, "en", detector=_scan(), policy=DEFAULT_POLICY)

    assert result["counts"] == [{"type": EntityType.ES_DNI, "found": 3, "hidden": 3}]
    assert [s["marker"] for s in result["reply"] if "marker" in s] == [
        "[[ES_DNI_1]]",
        "[[ES_DNI_2]]",
    ]


def test_a_text_without_personal_data() -> None:
    result = run_try("nada que ver aquí 👍", "es", detector=_scan(), policy=DEFAULT_POLICY)

    assert result["outcome"] == "masked"
    assert result["entities"] == [] and result["counts"] == []
    assert result["sent"] == [{"text": "nada que ver aquí 👍"}]
    assert [s for s in result["reply"] if "marker" in s] == []


def test_an_empty_text() -> None:
    result = run_try("", "en", detector=_scan(), policy=DEFAULT_POLICY)

    assert result["sent"] == [] and result["length_utf16"] == 0


def test_a_user_written_marker_is_escaped_and_never_restored() -> None:
    """Invariant 6: a marker the user wrote is not one this request emitted."""
    text = f"[[ES_DNI_1]] [[ES_DNI_9]] {SENTINEL_DNI}"

    result = run_try(text, "en", detector=_scan(), policy=DEFAULT_POLICY)

    assert _joined(result["sent"], "marker") == "[[!ES_DNI_1]] [[!ES_DNI_9]] [[ES_DNI_1]]"
    assert [s["marker"] for s in result["sent"] if "marker" in s] == ["[[ES_DNI_1]]"]
    assert [s["value"] for s in result["reply"] if "marker" in s] == [SENTINEL_DNI]
    assert "ES_DNI_9" not in _joined(result["reply"], "value")


def test_a_failing_detector_blocks_with_nothing_sent() -> None:
    def broken(_: str) -> list[Span]:
        raise RuntimeError(SENTINEL_DNI)

    result = run_try(SENTINEL_DNI, "en", detector=broken, policy=DEFAULT_POLICY)

    assert result["outcome"] == "blocked"
    assert result["blocked_reason"] == "detector_failed"
    assert result["sent"] == [] and result["reply"] == []
    assert result["entities"] == [] and result["counts"] == []
    assert SENTINEL_DNI not in repr(result)


def test_the_egress_guard_blocks_a_value_left_in_clear() -> None:
    def only_first(text: str) -> list[Span]:
        return [Span(0, 6, EntityType.PERSON, Layer.PATTERN, Confidence.HIGH)]

    result = run_try("Zoraya y Zoraya", "en", detector=only_first, policy=DEFAULT_POLICY)

    assert result["outcome"] == "blocked"
    assert result["blocked_reason"] == "egress_guard"
    assert result["sent"] == [] and result["reply"] == []
    assert result["counts"] == [{"type": EntityType.PERSON, "found": 1, "hidden": 1}]


def test_the_simulated_reply_gets_only_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[tuple[str, ...], str]] = []
    real = try_text.simulate_reply

    def spy(tokens: Any, lang: str) -> str:
        calls.append((tuple(tokens), lang))
        return real(tokens, lang)

    monkeypatch.setattr(try_text, "simulate_reply", spy)
    text = f"{SENTINEL_DNI} {EMAIL} {CIF}"

    run_try(text, "es", detector=_scan(), policy=DEFAULT_POLICY)

    assert calls == [(("ES_DNI_1", "EMAIL_1"), "es")]


def test_detector_failures_other_than_the_detector_propagate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_restore(*_: object, **__: object) -> str:
        raise RuntimeError("restore broke")

    monkeypatch.setattr(try_text, "restore", broken_restore)

    with pytest.raises(RuntimeError):
        run_try(SENTINEL_DNI, "en", detector=_scan(), policy=DEFAULT_POLICY)


# --- the import closure: no provider client can be reached -----------------------------------

MODULE = "antifaz.web.try_text"
FORBIDDEN_PROJECT = ("antifaz.providers", "antifaz.api", "antifaz.config", "antifaz.cli")
FORBIDDEN_EXTERNAL = {
    "httpx",
    "httpcore",
    "socket",
    "ssl",
    "http",
    "urllib3",
    "requests",
    "aiohttp",
    "pydantic",
    "pydantic_settings",
}


def test_the_try_module_never_reaches_a_provider_client() -> None:
    modules, external = _project_closure(SRC, "antifaz", MODULE)

    assert {MODULE, "antifaz.mask", "antifaz.guard", "antifaz.restore"} <= modules
    assert not {m for m in modules if m.startswith(FORBIDDEN_PROJECT)}, modules
    assert not external & FORBIDDEN_EXTERNAL, external & FORBIDDEN_EXTERNAL


def test_importing_the_try_module_loads_no_provider_client() -> None:
    """At runtime too. socket and ssl are not in this list: python-stdnum (the validators)
    imports them for its online lookups, which the detector never calls; the static check above
    is the one that keeps every project module in the closure off them."""
    watched = ["httpx", "httpcore", "antifaz.providers", "antifaz.api", "antifaz.config"]
    watched += ["pydantic", "pydantic_settings"]

    assert _loaded(MODULE, watched) == []


def test_the_try_module_takes_no_client() -> None:
    for function in (run_try, simulate_reply):
        for name in inspect.signature(function).parameters:
            assert "client" not in name and "http" not in name, name
    tree = ast.parse(inspect.getsource(try_text))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not {"httpx", "AsyncClient", "http_client"} & names
