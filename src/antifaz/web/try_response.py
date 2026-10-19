"""The closed JSON schema of POST /panel/api/try (issue 53, PR 11d-a, ADR-0018).

The only answer of the panel that carries personal values: the scoped exception to invariant 15.
They travel in `reply[].value` (the value put back in the simulated reply), in `sent[].text`
for the types the policy allows (they stay in clear, as the provider would get them) and in
`reply[].text`. Never the original text as a field: the page already has it.

- Every model refuses extra fields, is strict and frozen, and hides the input in its validation
  errors (`hide_input_in_errors`): a broken answer is a server bug, and its error must not print
  the values it was built from.
- Offsets (`entities[].start`, `end`) and `length_utf16` are UTF-16 code units, the unit of a
  JavaScript string (web/try_text.py explains why).
- A blocked answer has no sent or reply segments; `outcome` and `blocked_reason` agree.
- A marker is exactly the placeholder form `[[TYPE_N]]` (MARKER_PATTERN).

With web/status_response.py, one of the two web modules that import pydantic, and only plain
model pieces (tests/unit/test_web_never_reads_the_token.py): never a Secret type.
"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from antifaz.detect.types import EntityType
from antifaz.policy import VERSION_PATTERN
from antifaz.web.status import NerState

MARKER_PATTERN = r"^\[\[[A-Z]+(?:_[A-Z]+)*_[0-9]+\]\]$"
POLICY_VERSION_PATTERN = VERSION_PATTERN  # the policy's own rule (antifaz.policy)
MAX_SEGMENT_CHARACTERS = 65_536


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)


class Entity(_Closed):
    type: EntityType
    start: StrictInt = Field(ge=0)
    end: StrictInt = Field(ge=0)
    hidden: StrictBool

    @model_validator(mode="after")
    def _not_empty(self) -> Self:
        if self.end <= self.start:
            raise ValueError("an entity ends after it starts")
        return self


class TextPart(_Closed):
    text: str = Field(max_length=MAX_SEGMENT_CHARACTERS)


class SentMarker(_Closed):
    """A placeholder exactly as the provider would receive it."""

    marker: str = Field(pattern=MARKER_PATTERN, max_length=64)
    type: EntityType


class ReplyMarker(_Closed):
    """A placeholder of the simulated reply and the value restore() put back in its place."""

    marker: str = Field(pattern=MARKER_PATTERN, max_length=64)
    type: EntityType
    value: str = Field(min_length=1, max_length=MAX_SEGMENT_CHARACTERS)


class Count(_Closed):
    type: EntityType
    found: StrictInt = Field(ge=1)
    hidden: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def _hidden_of_found(self) -> Self:
        if self.hidden > self.found:
            raise ValueError("more hidden than found")
        return self


class TryResponse(_Closed):
    outcome: Literal["masked", "blocked"]
    blocked_reason: Literal["detector_failed", "egress_guard"] | None
    length_utf16: StrictInt = Field(ge=0)
    entities: list[Entity]
    sent: list[TextPart | SentMarker]
    reply: list[TextPart | ReplyMarker]
    counts: list[Count]
    policy_version: str = Field(pattern=POLICY_VERSION_PATTERN)
    ner: NerState

    @model_validator(mode="after")
    def _blocked_sends_nothing(self) -> Self:
        if (self.outcome == "blocked") != (self.blocked_reason is not None):
            raise ValueError("outcome and blocked_reason disagree")
        if self.outcome == "blocked" and (self.sent or self.reply):
            raise ValueError("a blocked answer has no segments")
        return self
