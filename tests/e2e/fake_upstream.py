"""Fake LLM provider for the end-to-end tests: records the bytes it gets and echoes the text.

Standard library only, so it runs inside the Antifaz image itself (no extra image to pull).
It answers OpenAI Chat (`POST /v1/chat/completions`) and Anthropic Messages
(`POST /v1/messages`), with or without streaming, echoing the last user text: what comes back
is exactly what reached the "provider", so the test can check that the client gets the real
values back while the provider only saw placeholders.

`GET /v1/models` (both providers use that path) records the request like a POST and answers
a one-model list, for `antifaz doctor --providers`.
`GET /_e2e/received` returns what it got (path, body, auth headers) for the test to inspect;
`GET /_e2e/health` is its healthcheck. It never logs a body. Synthetic data only.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

PORT = 9000
PIECE = 4  # streamed text goes out in pieces this long, so placeholders are cut across events
MAX_BODY = 1024 * 1024

_received: list[dict[str, Any]] = []
_lock = threading.Lock()


def last_user_text(body: dict[str, Any]) -> str:
    """The text of the last user message (a string or a list of text blocks/parts)."""
    users = [m for m in body.get("messages", []) if m.get("role") == "user"]
    if not users:
        return ""
    content = users[-1].get("content", "")
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in content if isinstance(part, dict))


def _sse(payload: dict[str, Any], event: str | None = None) -> bytes:
    head = f"event: {event}\n" if event else ""
    return f"{head}data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def _pieces(text: str) -> list[str]:
    return [text[i : i + PIECE] for i in range(0, len(text), PIECE)] or [""]


def openai_answer(model: str, text: str) -> dict[str, Any]:
    return {
        "id": "chatcmpl-e2e",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def openai_stream(model: str, text: str) -> list[bytes]:
    head = {"id": "chatcmpl-e2e", "object": "chat.completion.chunk", "created": 0, "model": model}
    events = [_sse({**head, "choices": [{"index": 0, "delta": {"role": "assistant"}}]})]
    events += [
        _sse({**head, "choices": [{"index": 0, "delta": {"content": piece}}]})
        for piece in _pieces(text)
    ]
    events.append(_sse({**head, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}))
    events.append(b"data: [DONE]\n\n")
    return events


def anthropic_answer(model: str, text: str) -> dict[str, Any]:
    return {
        "id": "msg_e2e",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def anthropic_stream(model: str, text: str) -> list[bytes]:
    message = {**anthropic_answer(model, ""), "content": [], "stop_reason": None}
    events = [
        _sse({"type": "message_start", "message": message}, "message_start"),
        _sse(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
            "content_block_start",
        ),
    ]
    events += [
        _sse(
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": p}},
            "content_block_delta",
        )
        for p in _pieces(text)
    ]
    events.append(_sse({"type": "content_block_stop", "index": 0}, "content_block_stop"))
    events.append(
        _sse(
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 1},
            },
            "message_delta",
        )
    )
    events.append(_sse({"type": "message_stop"}, "message_stop"))
    return events


class Handler(BaseHTTPRequestHandler):
    server_version = "fake-upstream"

    def log_message(self, format: str, *args: Any) -> None:
        """Silent: the test reads /_e2e/received, never the logs."""

    def _json(self, status: int, payload: object) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _stream(self, events: list[bytes]) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        for event in events:
            self.wfile.write(event)
            self.wfile.flush()
        self.close_connection = True

    def _record(self, raw: bytes) -> None:
        with _lock:
            _received.append(
                {
                    "method": self.command,
                    "path": self.path,
                    "body": raw.decode("utf-8", errors="replace"),
                    "authorization": self.headers.get("Authorization"),
                    "x-api-key": self.headers.get("x-api-key"),
                    "anthropic-version": self.headers.get("anthropic-version"),
                }
            )

    def do_GET(self) -> None:
        if self.path == "/v1/models":
            self._record(b"")
            self._json(200, {"object": "list", "data": [{"id": "fake-model"}]})
        elif self.path == "/_e2e/health":
            self._json(200, {"status": "ok"})
        elif self.path == "/_e2e/received":
            with _lock:
                self._json(200, list(_received))
        else:
            self._json(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(min(length, MAX_BODY))
        self._record(raw)
        try:
            body = json.loads(raw)
        except ValueError:
            self._json(400, {"error": {"message": "invalid json"}})
            return
        model = str(body.get("model", "fake-model"))
        text = "Recibido: " + last_user_text(body)
        stream = bool(body.get("stream"))
        if self.path == "/v1/chat/completions":
            if stream:
                self._stream(openai_stream(model, text))
            else:
                self._json(200, openai_answer(model, text))
        elif self.path == "/v1/messages":
            if stream:
                self._stream(anthropic_stream(model, text))
            else:
                self._json(200, anthropic_answer(model, text))
        else:
            self._json(404, {"error": {"message": "not found"}})


if __name__ == "__main__":
    # 0.0.0.0 in its own container: only the Compose network and 127.0.0.1 on the host reach it.
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()  # noqa: S104
