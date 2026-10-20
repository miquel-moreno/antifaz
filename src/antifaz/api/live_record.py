"""«En directo»: the one place where a request is recorded in the live feed (issue 53, PR 11e-1).

LiveRecorder is a plain ASGI middleware, inside the gate (a request the gate refused never
reaches it). For the five routes «En directo» shows, by exact (path, method), it puts a
LiveNote in scope["state"] before the route runs; the route and the error handlers note what
they decided there (antifaz.live.LiveNote: masked, blocked with a reason, upstream_error):

- the proxy routes, right after masking (mark_masked, with the vault's type counts) and when the
  provider answered an error (mark_upstream_error, also from the handler of the Upstream*
  errors); a block is noted by the AntifazBlocked handler (api/errors.py);
- /antifaz/scan after finding the spans, and «Prueba un texto» in its route (web/routes.py).

When the app returns, the note becomes one LiveEvent (a time rounded down to the minute) and is
recorded ONCE in app.state.live_feed. A request without a decision (no key, bad JSON, a body too
large) records nothing; neither does one that ended with an unexpected error (a 500).

The counter never reaches the proxy path: making the note, marking it and recording are each
wrapped, and a failure logs only the error's type. Recording is plain code (no await, no I/O,
bounded memory: antifaz.live). For a streamed answer the event is recorded when the stream ends.

Forbidden: logging the note or the event. Keeping anything of the request in the note but the
client's known name (never the User-Agent itself), the path's origin and the decision.
"""

import logging
import time
from collections.abc import Callable, Mapping
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

from antifaz.detect.types import EntityType
from antifaz.live import NOTE_KEY, LiveNote, Origin, client_of, note_of, reason_of
from antifaz.routing import route_path

ORIGINS: Mapping[tuple[str, str], Origin] = {
    ("/v1/chat/completions", "POST"): Origin.OPENAI_CHAT,
    ("/v1/messages", "POST"): Origin.ANTHROPIC_MESSAGES,
    ("/v1/messages/count_tokens", "POST"): Origin.COUNT_TOKENS,
    ("/antifaz/scan", "POST"): Origin.SCAN,
    ("/panel/api/try", "POST"): Origin.TRY,
}

logger = logging.getLogger("antifaz.live")


def _user_agent(scope: Scope) -> bytes | None:
    """The one User-Agent header, or None (absent or repeated: the client is OTHER)."""
    values = [value for name, value in scope.get("headers", []) if name.lower() == b"user-agent"]
    return values[0] if len(values) == 1 else None


def _safely(action: Callable[[], None]) -> None:
    try:
        action()
    except Exception as error:  # the counter never breaks a request; only the error's type
        logger.error("live feed failed: %s", type(error).__name__)


def mark_masked(scope: Scope, types: Mapping[EntityType, int]) -> None:
    """After masking: distinct hidden values per type (Vault.type_counts)."""
    note = note_of(scope)
    if note is not None:
        _safely(lambda: note.masked(types))


def mark_blocked(scope: Scope, error: BaseException) -> None:
    """An AntifazBlocked error: blocked, with its reason."""
    note = note_of(scope)
    if note is not None:
        _safely(lambda: note.blocked(reason_of(error)))


def mark_upstream_error(scope: Scope) -> None:
    """The provider failed or answered an error, after the request was masked and sent."""
    note = note_of(scope)
    if note is not None:
        _safely(note.upstream_error)


class LiveRecorder:
    def __init__(self, app: ASGIApp, clock: Callable[[], float] = time.time) -> None:
        self.app = app
        self._clock = clock

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        note: LiveNote | None = None
        if scope["type"] == "http":
            try:
                note = self._note(scope)
            except Exception as error:
                logger.error("live feed failed: %s", type(error).__name__)
        if note is None:
            await self.app(scope, receive, send)
            return
        await self.app(scope, receive, send)
        # Only after a normal return: an unexpected error (a 500) records nothing.
        _safely(lambda: self._record(scope, note))

    @staticmethod
    def _note(scope: Scope) -> LiveNote | None:
        origin = ORIGINS.get((route_path(scope), scope["method"]))
        if origin is None:
            return None
        note = LiveNote(origin, client_of(_user_agent(scope)))
        scope.setdefault("state", {})[NOTE_KEY] = note
        return note

    def _record(self, scope: Scope, note: LiveNote) -> None:
        event = note.event(at=self._clock())
        if event is not None:
            app: Any = scope["app"]
            app.state.live_feed.record(event)
