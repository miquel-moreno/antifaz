"""Panel sessions with a real logout (ADR-0018). Standard library only.

The browser holds a random id (`secrets.token_urlsafe(32)`, 256 bits) in its cookie. The server
keeps only the SHA-256 fingerprint of that id, with the creation time, the last use and the CSRF
token: a copy of the store does not hand out a usable cookie. A session lasts 8 hours at most
and 30 minutes without use. Expiry is checked when a session is looked up (no timers, no thread).

Whoever holds a live view of a session (the SSE stream of "En directo", in 11b-2) subscribes
with on_close() and is told, with the fingerprint and the reason, when that session is closed:
logout, expired or evicted. A subscriber that fails is logged (a fixed message and the error's
type only) and the others are still told: closing a session never fails because of one.

The store is bounded: when it is full, the OLDEST session (by creation) is evicted, its
subscribers told, and the new one is ALWAYS created. A full store never refuses a login.

Concurrency: no lock. It is correct only while every call runs on the same event loop thread
and nothing awaits in the middle of a call, so the 11b-2 routes that use it must be `async def`
(a plain `def` route would run in a thread pool). on_close callbacks are synchronous and must not
block: the SSE side only signals (asyncio.Event.set or loop.call_soon) and returns.

Forbidden: keeping the raw session id in any attribute. Logging an id, a fingerprint or a CSRF
token. Calling is_active() with a value the client sent (it takes a fingerprint, which only the
server computes: a client value would skip the id-to-fingerprint step).
"""

import dataclasses
import hashlib
import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

type CloseReason = Literal["logout", "expired", "evicted"]
type CloseCallback = Callable[[str, CloseReason], None]

MAX_AGE_SECONDS = 8 * 60 * 60
IDLE_SECONDS = 30 * 60
MAX_SESSIONS = 32
_ID_BYTES = 32

logger = logging.getLogger("antifaz.panel")


def fingerprint(session_id: str) -> str:
    """The SHA-256 of a session id, in hex: what the server stores instead of the id."""
    return hashlib.sha256(session_id.encode("utf-8", "surrogatepass")).hexdigest()


@dataclass(frozen=True)
class Session:
    """What the server keeps of a session. There is no field for the id itself, and its repr
    shows neither the fingerprint nor the CSRF token (a log line or a traceback must not)."""

    fingerprint: str = field(repr=False)
    created: float
    last_used: float
    csrf_token: str = field(repr=False)

    def valid_at(self, now: float) -> bool:
        """Younger than 8 hours and used in the last 30 minutes."""
        return now - self.created < MAX_AGE_SECONDS and now - self.last_used < IDLE_SECONDS


class SessionStore(Protocol):
    """Where sessions live. In memory today; PostgreSQL can take it later (ADR-0018, item 8)."""

    def create(self) -> tuple[str, Session]:
        """A new session: (the id for the cookie, what the server keeps)."""
        ...

    def get(self, session_id: str) -> Session | None:
        """The live session for this id, its last use refreshed; None if unknown or expired."""
        ...

    def delete(self, session_id: str, reason: CloseReason = "logout") -> bool:
        """Close the session now and tell the subscribers. True if it existed."""
        ...

    def is_active(self, key: str) -> bool:
        """Whether the session with this fingerprint is still live, without counting a use.
        Forbidden: calling it with a client-supplied value."""
        ...

    def on_close(self, callback: CloseCallback) -> Callable[[], None]:
        """Call `callback(fingerprint, reason)` whenever a session closes. Returns unsubscribe."""
        ...


class MemorySessionStore:
    """SessionStore in this process's memory: every session goes when the process restarts."""

    def __init__(
        self, clock: Callable[[], float] = time.monotonic, max_sessions: int = MAX_SESSIONS
    ) -> None:
        if max_sessions < 1:
            raise ValueError("max_sessions must be at least 1")
        self._clock = clock
        self._max_sessions = max_sessions
        self._sessions: dict[str, Session] = {}  # fingerprint -> session
        self._callbacks: list[CloseCallback] = []

    def __len__(self) -> int:
        return len(self._sessions)

    def create(self) -> tuple[str, Session]:
        now = self._clock()
        self._drop_expired(now)
        while len(self._sessions) >= self._max_sessions:
            oldest = min(self._sessions.values(), key=lambda session: session.created)
            self._close(oldest.fingerprint, "evicted")
        session_id = secrets.token_urlsafe(_ID_BYTES)
        session = Session(
            fingerprint=fingerprint(session_id),
            created=now,
            last_used=now,
            csrf_token=secrets.token_urlsafe(_ID_BYTES),
        )
        self._sessions[session.fingerprint] = session
        return session_id, session

    def get(self, session_id: str) -> Session | None:
        now = self._clock()  # once: the check and the new last use see the same instant
        key = fingerprint(session_id)
        session = self._live(key, now)
        if session is None:
            return None
        session = dataclasses.replace(session, last_used=now)
        self._sessions[key] = session
        return session

    def delete(self, session_id: str, reason: CloseReason = "logout") -> bool:
        key = fingerprint(session_id)
        if key not in self._sessions:
            return False
        self._close(key, reason)
        return True

    def is_active(self, key: str) -> bool:
        return self._live(key, self._clock()) is not None

    def on_close(self, callback: CloseCallback) -> Callable[[], None]:
        self._callbacks.append(callback)

        def unsubscribe() -> None:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

        return unsubscribe

    def _live(self, key: str, now: float) -> Session | None:
        session = self._sessions.get(key)
        if session is None:
            return None
        if not session.valid_at(now):
            self._close(key, "expired")
            return None
        return session

    def _drop_expired(self, now: float) -> None:
        for key in [k for k, session in self._sessions.items() if not session.valid_at(now)]:
            self._close(key, "expired")

    def _close(self, key: str, reason: CloseReason) -> None:
        """Forget the session, then tell every subscriber. Never raises."""
        del self._sessions[key]
        for callback in list(self._callbacks):
            try:
                callback(key, reason)
            except Exception as error:
                # Only the type: an exception's text could carry the fingerprint.
                logger.error("session close subscriber failed: %s", type(error).__name__)
