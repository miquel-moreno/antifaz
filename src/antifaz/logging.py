"""Structured JSON logging with a per-request id.

Only a fixed set of fields is written. Request and response bodies, headers and keys are never
logged. The httpx and httpcore loggers stay at WARNING whatever ANTIFAZ_LOG_LEVEL says (ADR-0013).
"""

import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime

# Their DEBUG output describes each request to the provider: never above WARNING.
QUIET_LOGGERS = ("httpx", "httpcore")

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    # Replace only our own handler (building the app twice must not log twice). Other handlers
    # stay: pytest's caplog, which the invariant 8 and 13 tests read, is one of them.
    ours = [h for h in root.handlers if isinstance(h.formatter, JsonFormatter)]
    for old in ours:
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(level.upper())
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
