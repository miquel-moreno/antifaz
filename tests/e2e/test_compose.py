"""End-to-end with the real image (issue 7a): Docker Compose, fake provider, synthetic data.

A client talks to Antifaz on 127.0.0.1; Antifaz talks to a fake provider that records the bytes
it gets and echoes the text. The provider must only ever see placeholders, the client must get
its values back (also streamed), and the container must run hardened: healthy, not root,
read-only, without capabilities, with no .env or dev tools inside the image.
"""

import json
from typing import Any

import httpx
import pytest

from tests.e2e.conftest import IMAGE, SUMMARY, Stack, _wait_healthy, docker

pytestmark = pytest.mark.e2e

# Synthetic values, invented for these tests (the DNI letter is computed, so it is valid).
_DNI_NUMBER = 48151623
DNI = f"{_DNI_NUMBER}{'TRWAGMYFPDXBNJZSQVHLCKE'[_DNI_NUMBER % 23]}"
EMAIL = "lucia.ejemplo@example.com"
MAX_IMAGE_MB = 300


def _message(tag: str) -> str:
    return f"[{tag}] Hola, mi DNI es {DNI} y mi correo es {EMAIL}. ¿Lo tienes?"


def _received_for(stack: Stack, tag: str) -> dict[str, Any]:
    matches = [r for r in stack.received() if tag in (r.get("body") or "")]
    assert len(matches) == 1, f"the fake provider got {len(matches)} requests tagged {tag}"
    return matches[0]


def _assert_provider_saw_only_placeholders(stack: Stack, received: dict[str, Any]) -> None:
    body = received["body"] or ""
    assert DNI not in body and str(_DNI_NUMBER) not in body
    assert EMAIL not in body and "lucia.ejemplo" not in body
    assert "[[" in body and "]]" in body
    everything = json.dumps(received)
    gateway_key_leaked = stack.gateway_key in everything
    assert not gateway_key_leaked, "the Antifaz key reached the provider"


def _openai_headers(stack: Stack) -> dict[str, str]:
    return {"Authorization": f"Bearer {stack.gateway_key}"}


def _anthropic_headers(stack: Stack) -> dict[str, str]:
    return {"x-api-key": stack.gateway_key, "anthropic-version": "2023-06-01"}


def _sse_payloads(text: str) -> list[Any]:
    lines = [line[len("data: ") :] for line in text.splitlines() if line.startswith("data: ")]
    return [json.loads(line) for line in lines if line != "[DONE]"]


# --- The container ---------------------------------------------------------------------------


def test_the_gateway_container_becomes_healthy(stack: Stack) -> None:
    assert _wait_healthy(stack) == "healthy"
    answer = httpx.get(f"{stack.gateway}/healthz", timeout=10)
    assert answer.status_code == 200
    assert answer.json()["status"] == "ok"


def test_a_request_without_the_key_is_refused(stack: Stack) -> None:
    answer = httpx.post(
        f"{stack.gateway}/v1/chat/completions",
        json={"model": "gpt-x", "messages": [{"role": "user", "content": "hola"}]},
        timeout=10,
    )
    assert answer.status_code == 401


def test_the_gateway_runs_as_a_fixed_non_root_user(stack: Stack) -> None:
    result = stack.compose("exec", "-T", "antifaz", "id", "-u")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "10001"
    user = docker("image", "inspect", "--format", "{{.Config.User}}", IMAGE).stdout.strip()
    assert user == "10001:10001"


def test_the_root_filesystem_is_read_only_and_only_tmp_is_writable(stack: Stack) -> None:
    write_app = stack.compose("exec", "-T", "antifaz", "python", "-c", "open('/app/x', 'w')")
    assert write_app.returncode != 0
    assert "Read-only file system" in write_app.stderr
    write_tmp = stack.compose(
        "exec", "-T", "antifaz", "python", "-c", "open('/tmp/x', 'w').write('ok')"
    )
    assert write_tmp.returncode == 0, write_tmp.stderr


def test_the_container_is_hardened_as_compose_says(stack: Stack) -> None:
    raw = docker("inspect", stack.container("antifaz")).stdout
    host = json.loads(raw)[0]["HostConfig"]
    assert host["ReadonlyRootfs"] is True
    assert host["CapDrop"] == ["ALL"]
    assert "no-new-privileges:true" in host["SecurityOpt"]
    assert host["Memory"] > 0 and host["NanoCpus"] > 0 and host["PidsLimit"] > 0
    bindings = host["PortBindings"]["8000/tcp"]
    assert [b["HostIp"] for b in bindings] == ["127.0.0.1"]


# --- The image -------------------------------------------------------------------------------


def test_the_image_holds_only_the_virtualenv_in_app() -> None:
    listing = docker("run", "--rm", "--entrypoint", "ls", IMAGE, "-a", "/app")
    assert listing.returncode == 0, listing.stderr
    assert sorted(listing.stdout.split()) == [".", "..", ".venv"]


def test_the_image_has_no_env_file_anywhere(stack: Stack) -> None:
    script = "find / -xdev \\( -name '.env' -o -name '.env.*' \\) -print 2>/dev/null; true"
    found = docker("run", "--rm", "--entrypoint", "sh", IMAGE, "-c", script, timeout=180)
    assert found.returncode == 0, found.stderr
    assert found.stdout.strip() == ""


def test_the_image_has_no_dev_tools_nor_http_clients() -> None:
    pytest_import = docker("run", "--rm", "--entrypoint", "python", IMAGE, "-c", "import pytest")
    assert pytest_import.returncode != 0
    tools = docker("run", "--rm", "--entrypoint", "sh", IMAGE, "-c", "command -v curl wget pip")
    assert tools.stdout.strip() == ""


def test_the_image_ships_no_pip() -> None:
    for module in ("pip", "ensurepip"):
        found = docker("run", "--rm", "--entrypoint", "python", IMAGE, "-m", module, "--version")
        assert found.returncode != 0, f"python -m {module} works in the image"


def test_image_size_is_recorded_and_bounded() -> None:
    size = int(docker("image", "inspect", "--format", "{{.Size}}", IMAGE).stdout.strip())
    megabytes = size / 1_000_000
    SUMMARY.append(f"e2e: image {IMAGE} is {megabytes:.1f} MB (uncompressed)")
    assert megabytes < MAX_IMAGE_MB


# --- Requests through the gateway ------------------------------------------------------------


def test_openai_chat_masks_for_the_provider_and_restores_for_the_client(stack: Stack) -> None:
    tag = "e2e-openai"
    answer = httpx.post(
        f"{stack.gateway}/v1/chat/completions",
        headers=_openai_headers(stack),
        json={"model": "gpt-x", "messages": [{"role": "user", "content": _message(tag)}]},
        timeout=30,
    )
    assert answer.status_code == 200, answer.text
    content = answer.json()["choices"][0]["message"]["content"]
    assert content == f"Recibido: {_message(tag)}"

    received = _received_for(stack, tag)
    assert received["path"] == "/v1/chat/completions"
    _assert_provider_saw_only_placeholders(stack, received)
    right_key = received["authorization"] == f"Bearer {stack.openai_key}"
    assert right_key, "the provider did not get the OpenAI key from the env file"


def test_openai_chat_streaming_restores_values_cut_across_events(stack: Stack) -> None:
    tag = "e2e-openai-stream"
    body = {
        "model": "gpt-x",
        "stream": True,
        "messages": [{"role": "user", "content": _message(tag)}],
    }
    with httpx.stream(
        "POST",
        f"{stack.gateway}/v1/chat/completions",
        headers=_openai_headers(stack),
        json=body,
        timeout=30,
    ) as answer:
        assert answer.status_code == 200
        assert answer.headers["content-type"].startswith("text/event-stream")
        text = answer.read().decode("utf-8")
    pieces = [
        choice["delta"].get("content") or ""
        for event in _sse_payloads(text)
        for choice in event["choices"]
    ]
    assert "".join(pieces) == f"Recibido: {_message(tag)}"
    _assert_provider_saw_only_placeholders(stack, _received_for(stack, tag))


def test_anthropic_messages_masks_and_restores(stack: Stack) -> None:
    tag = "e2e-anthropic"
    answer = httpx.post(
        f"{stack.gateway}/v1/messages",
        headers=_anthropic_headers(stack),
        json={
            "model": "claude-x",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": _message(tag)}],
        },
        timeout=30,
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["content"][0]["text"] == f"Recibido: {_message(tag)}"

    received = _received_for(stack, tag)
    assert received["path"] == "/v1/messages"
    _assert_provider_saw_only_placeholders(stack, received)
    right_key = received["x-api-key"] == stack.anthropic_key
    assert right_key, "the provider did not get the Anthropic key from the env file"


def test_anthropic_messages_streaming_restores_values(stack: Stack) -> None:
    tag = "e2e-anthropic-stream"
    with httpx.stream(
        "POST",
        f"{stack.gateway}/v1/messages",
        headers=_anthropic_headers(stack),
        json={
            "model": "claude-x",
            "max_tokens": 64,
            "stream": True,
            "messages": [{"role": "user", "content": _message(tag)}],
        },
        timeout=30,
    ) as answer:
        assert answer.status_code == 200
        text = answer.read().decode("utf-8")
    pieces = [
        event["delta"].get("text", "")
        for event in _sse_payloads(text)
        if event.get("type") == "content_block_delta"
    ]
    assert "".join(pieces) == f"Recibido: {_message(tag)}"
    _assert_provider_saw_only_placeholders(stack, _received_for(stack, tag))


def test_container_logs_hold_no_values_and_no_keys(stack: Stack) -> None:
    # Make sure at least one request went through before reading the logs.
    httpx.post(
        f"{stack.gateway}/v1/chat/completions",
        headers=_openai_headers(stack),
        json={"model": "gpt-x", "messages": [{"role": "user", "content": _message("e2e-logs")}]},
        timeout=30,
    )
    # The DNI in a query string (refused by the detector) and in an unknown path (404).
    in_query = httpx.get(
        f"{stack.gateway}/v1/models",
        params={"after_id": DNI},
        headers=_anthropic_headers(stack),
        timeout=30,
    )
    assert in_query.status_code == 400
    in_path = httpx.get(f"{stack.gateway}/v1/{DNI}", headers=_openai_headers(stack), timeout=30)
    assert in_path.status_code == 404
    for answer in (in_query, in_path):
        assert DNI not in answer.text
    logs = stack.compose("logs", "--no-color", "antifaz").stdout
    assert logs, "no logs to check"
    assert DNI not in logs and EMAIL not in logs
    keys = (stack.gateway_key, stack.openai_key, stack.anthropic_key)
    key_in_logs = any(key in logs for key in keys)
    assert not key_in_logs, "a key appeared in the container logs"
