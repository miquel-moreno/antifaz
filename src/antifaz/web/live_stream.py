"""«En directo»'s SSE streams (issue 53, PR 11e-1, ADR-0018): GET /panel/api/live, one per tab.

LiveStreams.stream(key) is the body of one stream, for the session with fingerprint `key` (the
door found it live; only the server computes fingerprints). In this order:

1. Limits, checked when the stream starts: at most MAX_STREAMS (5) per process and
   MAX_PER_SESSION (2) per session. Over either, the answer is still 200, with one `end: busy`
   and nothing else (no snapshot). After shutdown, `end: shutdown`.
2. It subscribes to the feed (a bounded queue, antifaz.live) and to the session's close
   (sessions.watch): a logout, a new login over the old cookie or an eviction cut it at once.
3. `snapshot` with the feed now, then one `request` per recorded event. If the queue filled up
   in between (the client is slow), a fresh `snapshot` instead of what was missed.
4. The deadline is the earliest of MAX_STREAM_SECONDS (30 min) from the start and the session's
   own time left (sessions.seconds_left, without counting a use). At the session's deadline it
   is checked again: extended by «Seguir» (POST /panel/api/session), the stream goes on with
   the new deadline; over, `end: expired`. At 30 minutes, `end: max_age`.
5. A heartbeat comment every HEARTBEAT_SECONDS (25 s), only while the stream is open.
6. Every way out (an end, the client leaving, cancellation) unsubscribes from the feed and the
   session and frees the place.

Watching is never a use of the session (door.NOT_A_USE): «En directo» left open does not keep a
session alive, and it ends when the session would.

Timers: only while a stream is open, one `asyncio.timeout` around its wait. With no stream open,
nothing of this module runs: no task, no timer, no polling (zero CPU when nobody watches).

Concurrency: like the session store, no lock. Every call runs on the event loop thread, and the
counting of places (the limits) happens with no await between the check and the claim.

Forbidden: a `retry:` or `id:` field. Keeping anything of the request but the fingerprint.
Counting the stream as a use of the session. A timer or task while no stream is open.
"""

import asyncio
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field

from antifaz.live import LiveFeed
from antifaz.web.live_response import HEARTBEAT, EndReason, end_frame, request_frame, snapshot_frame
from antifaz.web.sessions import CloseReason, SessionStore, watch

MAX_STREAMS = 5
MAX_PER_SESSION = 2
MAX_STREAM_SECONDS = 30 * 60
HEARTBEAT_SECONDS = 25.0
EVENT_STREAM = "text/event-stream"

# Why a session closed -> why its streams end. An eviction (the store was full and a newer login
# took its place) reads as replaced; a session closed for its scheme, or already gone, as expired.
_ENDS: dict[CloseReason, EndReason] = {
    "logout": EndReason.LOGOUT,
    "replaced": EndReason.REPLACED,
    "evicted": EndReason.REPLACED,
    "expired": EndReason.EXPIRED,
    "scheme_mismatch": EndReason.EXPIRED,
    "gone": EndReason.EXPIRED,
}


def accepts_event_stream(values: Iterable[str]) -> bool:
    """Whether one of the Accept media ranges is exactly text/event-stream (parameters such as
    q= ignored). `*/*` alone is not enough: EventSource always asks for it by name."""
    for value in values:
        for item in value[:1024].split(","):
            if item.split(";", 1)[0].strip().lower() == EVENT_STREAM:
                return True
    return False


@dataclass(eq=False)
class _Stream:
    key: str = field(repr=False)
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    end: EndReason | None = None

    def cut(self, reason: EndReason) -> None:
        """Only signals (it runs inside a session's close or the shutdown): never blocks."""
        if self.end is None:
            self.end = reason
        self.wake.set()


class LiveStreams:
    """The open streams of this process. The limits and times are attributes so that tests can
    shorten them; the gateway uses the defaults."""

    def __init__(
        self,
        feed: LiveFeed,
        sessions: SessionStore,
        *,
        max_streams: int = MAX_STREAMS,
        per_session: int = MAX_PER_SESSION,
        max_seconds: float = MAX_STREAM_SECONDS,
        heartbeat_seconds: float | None = HEARTBEAT_SECONDS,
    ) -> None:
        self._feed = feed
        self._sessions = sessions
        self.max_streams = max_streams
        self.per_session = per_session
        self.max_seconds = max_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self._open: list[_Stream] = []
        self._closed = False

    @property
    def open_streams(self) -> int:
        return len(self._open)

    def close_all(self) -> None:
        """Shutdown: every open stream ends with `end: shutdown`, and no new one starts."""
        self._closed = True
        for stream in list(self._open):
            stream.cut(EndReason.SHUTDOWN)

    def _claim(self, key: str) -> _Stream | EndReason:
        """A place for a new stream of `key`, or why there is none. No await: check and claim."""
        if self._closed:
            return EndReason.SHUTDOWN
        mine = sum(1 for stream in self._open if stream.key == key)
        if len(self._open) >= self.max_streams or mine >= self.per_session:
            return EndReason.BUSY
        stream = _Stream(key)
        self._open.append(stream)
        return stream

    def _session_deadline(self, key: str, now: float) -> float | None:
        """When the session would end by itself (loop time), or None if it is not live now."""
        left = self._sessions.seconds_left(key)
        return None if left is None else now + max(0.0, min(left))

    async def stream(self, key: str) -> AsyncIterator[bytes]:
        """The frames of one stream for the live session `key` (a server fingerprint)."""
        claimed = self._claim(key)
        if isinstance(claimed, EndReason):
            yield end_frame(claimed)
            return
        stream = claimed
        subscription = self._feed.subscribe(stream.wake.set)
        unwatch = watch(self._sessions, key, lambda reason: stream.cut(_ENDS[reason]))
        try:
            loop = asyncio.get_running_loop()
            started = loop.time()
            session_end = self._session_deadline(key, started)
            if session_end is None or stream.end is not None:  # closed before it started
                yield end_frame(stream.end or EndReason.EXPIRED)
                return
            yield snapshot_frame(self._feed.snapshot())
            beat = started
            while True:
                now = loop.time()
                wake_at = min(started + self.max_seconds, session_end)
                if self.heartbeat_seconds is not None:
                    wake_at = min(wake_at, beat + self.heartbeat_seconds)
                if not stream.wake.is_set() and wake_at > now:
                    try:
                        async with asyncio.timeout(wake_at - now):
                            await stream.wake.wait()
                    except TimeoutError:
                        pass
                stream.wake.clear()
                if stream.end is not None:
                    yield end_frame(stream.end)
                    return
                overflowed, events = subscription.drain()
                if overflowed:
                    yield snapshot_frame(self._feed.snapshot())
                else:
                    for event in events:
                        yield request_frame(event)
                now = loop.time()
                if now >= started + self.max_seconds:
                    yield end_frame(EndReason.MAX_AGE)
                    return
                if now >= session_end:
                    moved = self._session_deadline(key, now)  # «Seguir» may have extended it
                    if moved is None:
                        yield end_frame(stream.end or EndReason.EXPIRED)
                        return
                    session_end = moved
                if self.heartbeat_seconds is not None and now >= beat + self.heartbeat_seconds:
                    beat = now
                    yield HEARTBEAT
        finally:
            unwatch()
            subscription.close()
            self._open.remove(stream)


__all__ = [
    "EVENT_STREAM",
    "HEARTBEAT_SECONDS",
    "MAX_PER_SESSION",
    "MAX_STREAMS",
    "MAX_STREAM_SECONDS",
    "LiveStreams",
    "accepts_event_stream",
]
