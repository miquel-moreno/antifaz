"""«Prueba un texto» without the AI (issue 53, PR 11d-a, ADR-0018): the body, the pipeline and the
simulated reply. No framework: the route (web/routes.py) reads the body and answers; the gateway
(api/app.py `_panel`) runs run_try() in a worker thread, behind the Try limiter (one thread)
and then the proxy's mask limiter.

- parse_body(raw, languages): exactly {"text": str, "lang": a loaded catalog code}, no repeated
  key, no NaN or Infinity, no lone surrogate, valid UTF-8 without BOM; anything else is
  TryBodyError, whose message is fixed and never quotes the input (raised outside any except
  block, so no decode error is even its context). A text over MAX_TEXT_BYTES UTF-8 bytes is
  TextTooLongError. The raw body itself is capped by the route at MAX_BODY_BYTES (enough for the
  largest text written as \\u escapes) and read within BODY_SECONDS.
- run_try(text, lang, detector, policy): the REAL pipeline, the same functions as the proxy, no
  copy: antifaz.mask.mask() with the gateway's detector (the NER too when it is on) and its
  policy, antifaz.guard.check() on the masked text, a simulated reply built ONLY from the
  emitted marker tokens, and antifaz.restore.restore() of that reply. No AI is called and this
  module cannot reach one: it never imports antifaz.providers, antifaz.api, httpx, socket or ssl
  (tests/unit/test_web_try_text.py walks the import closure) and takes no client. The answer is
  a dict for the closed schema of web/try_response.py; the original text is never in it.
- A failing detector is outcome "blocked", reason "detector_failed"; a value the guard still
  sees, "egress_guard". Both send nothing (no sent or reply segments). Any other error
  propagates and the route answers a fixed 500.
- simulate_reply(tokens, lang): gets only tokens such as "ES_DNI_1" (checked one by one), never
  the text or the vault, so nothing but a marker of this request can reach the reply. Fixed,
  deterministic phrases per language (REPLIES), English for a language without its own; at most
  MAX_LISTED markers are named, then "and N more". The phrases live here, not in the catalogs:
  the catalogs hold the page's texts, and a test forbids keys no template uses.
- Segments: the masked text and the simulated reply are cut with restore()'s own pattern
  (placeholder_parts). `sent` joined gives the masked text the provider would receive (allowed
  types stay in clear, a marker the user wrote stays escaped as "[[!"); `reply` joined (texts
  and values) is exactly restore() of the whole simulated reply.

The offsets unit. Python indexes a string by code points; JavaScript by UTF-16 code units. An
emoji outside the Basic Multilingual Plane is one code point but two units, so a Python offset
would point the page at the wrong characters after the first one. The answer therefore gives
every offset in UTF-16 units (utf16_offsets) and `length_utf16`, the UTF-16 length of the text
it processed: the page checks it against its own snapshot of the text before it marks anything.
Grapheme clusters (a ZWJ family, a flag, a letter with combining marks) are several units; an
entity never starts or ends inside a surrogate pair because both offsets come from code points.

Memory: the text and the values live in Python strings for the request only. Python cannot wipe
a string; it is freed when nothing refers to it any more and the allocator may reuse the memory
later. Nothing here keeps a reference after the answer, writes to disk or logs.

Forbidden: logging anything of the text, the values or the vault. Passing the text or the vault
to simulate_reply. Importing a provider client or an HTTP library.
"""

import json
import re
import time
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from antifaz import guard
from antifaz.detect.types import EntityType
from antifaz.errors import DetectorFailed, EgressBlocked
from antifaz.mask import Detector, FoundSpan, MaskResult, mask
from antifaz.policy import Policy
from antifaz.restore import placeholder_parts, restore
from antifaz.vault import Vault

MAX_BODY_BYTES = 128 * 1024
MAX_TEXT_BYTES = 20_480
BODY_SECONDS = 5
MAX_LISTED = 10

_TOKEN = re.compile(r"[A-Z]+(?:_[A-Z]+)*_[0-9]+")
_KEYS = frozenset({"text", "lang"})


class TryBodyError(ValueError):
    """The body is not exactly {"text": str, "lang": a loaded code}. Fixed message."""

    message = "the body must be JSON with exactly a text and a loaded language"

    def __init__(self) -> None:
        super().__init__(self.message)


class TextTooLongError(ValueError):
    """The text is longer than MAX_TEXT_BYTES in UTF-8. Fixed message."""

    message = "the text is too long"

    def __init__(self) -> None:
        super().__init__(self.message)


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("repeated key")  # the key is never in the message
    return dict(pairs)


def _reject_constant(_: str) -> object:
    raise ValueError("NaN and Infinity are not valid JSON")


def _utf8_size(text: str) -> int | None:
    """Bytes of `text` in UTF-8, or None if it cannot be encoded (a lone surrogate)."""
    try:
        return len(text.encode("utf-8"))
    except UnicodeEncodeError:
        return None


def parse_body(raw: bytes, languages: Collection[str]) -> tuple[str, str]:
    """(text, lang) of a body that is exactly {"text": str, "lang": one of `languages`}."""
    data: object = None
    try:
        data = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_keys, parse_constant=_reject_constant
        )
    except (ValueError, RecursionError):  # bad UTF-8 and bad JSON are ValueErrors
        data = None
    text: object = None
    lang: object = None
    if isinstance(data, dict) and data.keys() == _KEYS:
        text, lang = data["text"], data["lang"]
    good = type(text) is str and type(lang) is str and lang in languages
    size = _utf8_size(text) if isinstance(text, str) else None
    # Raised outside the except block: a decode error (which may quote the body) is dropped.
    if not good or size is None or not isinstance(text, str) or not isinstance(lang, str):
        raise TryBodyError() from None
    if size > MAX_TEXT_BYTES:
        raise TextTooLongError()
    return text, lang


def utf16_length(text: str) -> int:
    """The length of `text` in UTF-16 code units (JavaScript's `text.length`)."""
    return len(text) + sum(1 for char in text if ord(char) > 0xFFFF)


def utf16_offsets(text: str, positions: Iterable[int]) -> dict[int, int]:
    """Each code-point position of `text` (0 to len(text)) as a UTF-16 offset."""
    wanted = sorted(set(positions))
    if wanted and (wanted[0] < 0 or wanted[-1] > len(text)):
        raise ValueError("a position outside the text")
    out: dict[int, int] = {}
    units = 0
    index = 0
    for position in wanted:
        for char in text[index:position]:
            units += 2 if ord(char) > 0xFFFF else 1
        index = position
        out[position] = units
    return out


@dataclass(frozen=True)
class Phrases:
    """The fixed text of a simulated reply in one language: no value can be in it."""

    none: str  # the text had nothing to hide
    some: str  # {markers}: the markers named
    more: str  # {count}: how many more were not named
    comma: str
    last: str  # between the last two items


REPLIES: Mapping[str, Phrases] = {
    "en": Phrases(
        none=(
            "Simulated reply (no AI was called). Your text had nothing to hide, so the AI "
            "would receive it as you wrote it."
        ),
        some=(
            "Simulated reply (no AI was called). The AI only saw markers; Antifaz puts the "
            "real values back in their place: {markers}."
        ),
        more="{count} more",
        comma=", ",
        last=" and ",
    ),
    "es": Phrases(
        none=(
            "Respuesta simulada (no se ha llamado a ninguna IA). Tu texto no tenía nada que "
            "ocultar, así que la IA lo recibiría tal como lo escribiste."
        ),
        some=(
            "Respuesta simulada (no se ha llamado a ninguna IA). La IA solo vio marcadores; "
            "Antifaz devuelve los valores reales a su sitio: {markers}."
        ),
        more="{count} más",
        comma=", ",
        last=" y ",
    ),
}
FALLBACK = "en"


def simulate_reply(tokens: Sequence[str], lang: str) -> str:
    """A reply that names each marker token once, in order, from fixed phrases only."""
    for token in tokens:
        if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
            raise ValueError("simulate_reply only takes marker tokens")  # never the value
    phrases = REPLIES.get(lang, REPLIES[FALLBACK])
    if not tokens:
        return phrases.none
    items = [f"[[{token}]]" for token in tokens[:MAX_LISTED]]
    if len(tokens) > MAX_LISTED:
        items.append(phrases.more.format(count=len(tokens) - MAX_LISTED))
    listed = items[0] if len(items) == 1 else phrases.comma.join(items[:-1]) + phrases.last
    if len(items) > 1:
        listed += items[-1]
    return phrases.some.format(markers=listed)


def _merge(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Adjacent text segments joined (escapes are cut from their text by nothing else)."""
    out: list[dict[str, Any]] = []
    for segment in segments:
        if "text" in segment and out and "text" in out[-1]:
            out[-1] = {"text": out[-1]["text"] + segment["text"]}
        elif segment.get("text", True):  # no empty text segment
            out.append(segment)
    return out


def _sent(masked: str, types: Mapping[str, EntityType]) -> list[dict[str, Any]]:
    """The masked text as the provider would get it: texts and this request's markers."""
    return _merge(
        [
            {"marker": raw, "type": types[token]}
            if token is not None and token in types
            else {"text": raw}
            for raw, token in placeholder_parts(masked)
        ]
    )


def _reply(simulated: str, types: Mapping[str, EntityType], vault: Vault) -> list[dict[str, Any]]:
    """The simulated reply restored part by part: texts, and each marker with its value."""
    return _merge(
        [
            {"marker": raw, "type": types[token], "value": restore(raw, vault)}
            if token is not None and token in types
            else {"text": restore(raw, vault)}
            for raw, token in placeholder_parts(simulated)
        ]
    )


def _counts(spans: Sequence[FoundSpan]) -> list[dict[str, Any]]:
    found: dict[EntityType, list[int]] = {}
    for span in spans:
        tally = found.setdefault(span.type, [0, 0])
        tally[0] += 1
        tally[1] += span.hidden
    return [
        {"type": entity, "found": found[entity][0], "hidden": found[entity][1]}
        for entity in EntityType
        if entity in found
    ]


def _entities(text: str, spans: Sequence[FoundSpan]) -> list[dict[str, Any]]:
    offsets = utf16_offsets(text, [p for span in spans for p in (span.start, span.end)])
    return [
        {"type": s.type, "start": offsets[s.start], "end": offsets[s.end], "hidden": s.hidden}
        for s in spans
    ]


def run_try(text: str, lang: str, *, detector: Detector, policy: Policy) -> dict[str, Any]:
    """mask -> guard -> simulated reply (markers only) -> restore. Synchronous: run it in a
    worker thread. The answer never holds the original text."""
    started = time.perf_counter()
    answer: dict[str, Any] = {
        "outcome": "blocked",
        "blocked_reason": None,
        "length_utf16": utf16_length(text),
        "entities": [],
        "sent": [],
        "reply": [],
        "counts": [],
        "policy_version": policy.version,
    }
    result: MaskResult | None = None
    try:
        result = mask(text, policy, detector=detector)
    except DetectorFailed:
        answer["blocked_reason"] = "detector_failed"
    if result is not None:
        spans = result.found_spans[0]
        answer["entities"] = _entities(text, spans)
        answer["counts"] = _counts(spans)
        blocked = False
        try:
            guard.check(result.text, result.vault)
        except EgressBlocked:
            blocked = True
        if blocked:
            answer["blocked_reason"] = "egress_guard"
        else:
            types = {s.token: s.type for s in spans if s.token is not None}
            simulated = simulate_reply(list(types), lang)
            answer["outcome"] = "masked"
            answer["sent"] = _sent(result.text, types)
            answer["reply"] = _reply(simulated, types, result.vault)
    answer["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    return answer


__all__ = [
    "BODY_SECONDS",
    "MAX_BODY_BYTES",
    "MAX_LISTED",
    "MAX_TEXT_BYTES",
    "REPLIES",
    "TextTooLongError",
    "TryBodyError",
    "parse_body",
    "run_try",
    "simulate_reply",
    "utf16_length",
    "utf16_offsets",
]
