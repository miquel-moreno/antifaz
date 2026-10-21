"""The closed JSON schema of GET /panel/api/live's events (issue 53, PR 11e-1, ADR-0018).

Three events, each one SSE frame `event: <name>` + one `data:` line of JSON + a blank line:

- `snapshot`: the feed now (antifaz.live.LiveSnapshot): the recent events newest first, the
  proxy's hidden values per type, outcomes and (11e-2) blocks per reason, `tries` and `scans`
  (apart from the totals), the uptime in seconds and how many worker processes the server runs
  (more than one: «datos de este proceso»).
- `request`: one LiveEvent.
- `end`: why the stream ends (EndReason); the stream closes right after.

Every JSON leaf is an enum value or an integer: types, outcomes and blocks go as lists of
{"type"/"outcome"/"reason", "count"}, never as free-text keys. Every model refuses extra fields, is
strict and frozen, and hides its input in validation errors. There is never a `retry:` field
(the browser does not reconnect by itself) nor an `id:`.

With web/status_response.py and web/try_response.py, one of the web modules that import pydantic,
and only plain model pieces (tests/unit/test_web_never_reads_the_token.py).
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from antifaz.detect.types import EntityType
from antifaz.live import RECENT, Client, LiveEvent, LiveSnapshot, Origin, Outcome, Provider, Reason


class EndReason(StrEnum):
    LOGOUT = "logout"
    EXPIRED = "expired"
    REPLACED = "replaced"
    BUSY = "busy"
    MAX_AGE = "max_age"
    SHUTDOWN = "shutdown"


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)


class TypeCount(_Closed):
    type: EntityType
    count: StrictInt = Field(ge=1)


class OutcomeCount(_Closed):
    outcome: Outcome
    count: StrictInt = Field(ge=0)


class ReasonCount(_Closed):
    reason: Reason
    count: StrictInt = Field(ge=1)


class RequestEvent(_Closed):
    origin: Origin
    client: Client
    outcome: Outcome
    reason: Reason
    provider: Provider
    at: StrictInt = Field(ge=0, multiple_of=60)  # Unix time, rounded down to the minute
    types: list[TypeCount]


class SnapshotEvent(_Closed):
    recent: list[RequestEvent] = Field(max_length=RECENT)
    hidden: list[TypeCount]
    outcomes: list[OutcomeCount]
    blocks: list[ReasonCount]
    tries: StrictInt = Field(ge=0)
    scans: StrictInt = Field(ge=0)
    uptime_seconds: StrictInt = Field(ge=0)
    processes: StrictInt = Field(ge=1)


class EndEvent(_Closed):
    reason: EndReason


def _request(event: LiveEvent) -> RequestEvent:
    return RequestEvent(
        origin=event.origin,
        client=event.client,
        outcome=event.outcome,
        reason=event.reason,
        provider=event.provider,
        at=event.at,
        types=[TypeCount(type=entity, count=count) for entity, count in event.types.items()],
    )


def _frame(name: str, model: BaseModel) -> bytes:
    return (
        b"event: " + name.encode("ascii") + b"\ndata: " + model.model_dump_json().encode() + b"\n\n"
    )


def snapshot_frame(snapshot: LiveSnapshot) -> bytes:
    model = SnapshotEvent(
        recent=[_request(event) for event in snapshot.recent],
        hidden=[TypeCount(type=entity, count=count) for entity, count in snapshot.hidden.items()],
        outcomes=[OutcomeCount(outcome=o, count=count) for o, count in snapshot.outcomes.items()],
        blocks=[ReasonCount(reason=r, count=count) for r, count in snapshot.blocks.items()],
        tries=snapshot.tries,
        scans=snapshot.scans,
        uptime_seconds=snapshot.uptime_seconds,
        processes=snapshot.processes,
    )
    return _frame("snapshot", model)


def request_frame(event: LiveEvent) -> bytes:
    return _frame("request", _request(event))


def end_frame(reason: EndReason) -> bytes:
    return _frame("end", EndEvent(reason=reason))


# A comment line: it keeps proxies from closing an idle stream; EventSource ignores it.
HEARTBEAT = b":\n\n"
