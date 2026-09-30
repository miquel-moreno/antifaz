"""`antifaz verify`: plants synthetic data with the user's configuration, never calls a provider.

It must pass with a working gateway, fail (exit 1) when the masker is sabotaged, and never
print a planted value or a key.
"""

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from antifaz.cli import main
from antifaz.cli.verify import PLANTED_TEXT, PLANTS, probes, verify
from antifaz.config import Settings
from antifaz.detect.scan import scan
from antifaz.mask import mask as real_mask
from tests.conftest import forbid_network

GATEWAY_KEY = "verify-test-gateway-key-0123456789abcdef"
PROVIDER_KEY = "verify-test-provider-key-not-real"


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "antifaz_api_key": SecretStr(GATEWAY_KEY),
        "openai_api_key": SecretStr(PROVIDER_KEY),
        "anthropic_api_key": SecretStr(PROVIDER_KEY),
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """verify must never open a connection nor build the real upstream client."""

    forbid_network(monkeypatch)

    def no_real_client(*_: object) -> None:
        raise AssertionError("antifaz verify built the real provider client")

    monkeypatch.setattr("antifaz.api.app.build_client", no_real_client)


def _output_is_clean(text: str) -> None:
    """No planted value (in any spacing) and no key in what verify printed."""
    compact = "".join(c for c in text.casefold() if c.isalnum())
    for plant in PLANTS:
        assert plant.value not in text
        assert "".join(c for c in plant.value.casefold() if c.isalnum()) not in compact
    assert GATEWAY_KEY not in text
    assert PROVIDER_KEY not in text


def test_every_planted_value_is_detected_in_the_planted_text() -> None:
    found = {(span.type, PLANTED_TEXT[span.start : span.end]) for span in scan(PLANTED_TEXT)}
    assert found == {(plant.type, plant.value) for plant in PLANTS}


def test_probes_cover_both_endpoints_streaming_and_every_place() -> None:
    battery = probes()
    routes = {probe.route for probe in battery}
    assert routes == {"openai", "anthropic", "count_tokens"}
    assert any(p.stream and p.route == "openai" for p in battery)
    assert any(p.stream and p.route == "anthropic" for p in battery)
    locations = " ".join(p.location for p in battery)
    for place in ("system", "tool call arguments", "tool result", "tool_use input", "tool_result"):
        assert place in locations
    assert sum(not p.planted for p in battery) == 2  # one key-echo probe per endpoint


def test_verify_passes_with_a_working_gateway(capsys: pytest.CaptureFixture[str]) -> None:
    assert verify(_settings()) == 0

    captured = capsys.readouterr()
    assert "PASS" in captured.out
    assert "ES_DNI" in captured.out
    _output_is_clean(captured.out + captured.err)


def test_verify_does_not_need_provider_keys(capsys: pytest.CaptureFixture[str]) -> None:
    settings = _settings(openai_api_key=None, anthropic_api_key=None)

    assert verify(settings) == 0
    assert "PASS" in capsys.readouterr().out


def test_cli_reads_the_configuration_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setenv("ANTIFAZ_API_KEY", GATEWAY_KEY)
    monkeypatch.setenv("ANTIFAZ_OPENAI_API_KEY", PROVIDER_KEY)

    assert main(["verify"]) == 0

    captured = capsys.readouterr()
    assert "PASS" in captured.out
    _output_is_clean(captured.out + captured.err)


def test_cli_reads_the_dotenv_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTIFAZ_API_KEY", raising=False)
    (tmp_path / ".env").write_text(f"ANTIFAZ_API_KEY={GATEWAY_KEY}\n", encoding="utf-8")

    assert main(["verify"]) == 0
    assert "PASS" in capsys.readouterr().out


def test_unsafe_configuration_exits_2_naming_the_variable_only(
    capsys: pytest.CaptureFixture[str],
) -> None:
    example = "change-me-to-a-random-value-of-32-characters-or-more"

    assert verify(_settings(antifaz_api_key=SecretStr(example))) == 2

    captured = capsys.readouterr()
    assert "ANTIFAZ_API_KEY" in captured.err
    assert example not in captured.out + captured.err


def test_unreadable_configuration_exits_2_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTIFAZ_API_KEY", GATEWAY_KEY)
    monkeypatch.setenv("ANTIFAZ_MAX_BODY_BYTES", "12345678Z")

    assert main(["verify"]) == 2

    captured = capsys.readouterr()
    assert "max_body_bytes" in captured.err
    assert "12345678Z" not in captured.out + captured.err
    assert GATEWAY_KEY not in captured.out + captured.err


def _blind_mask(texts: Sequence[str], *args: Any, **kwargs: Any) -> Any:
    """A sabotaged masker: detects nothing, so nothing is masked and the vault is empty."""
    kwargs["detector"] = lambda text: []
    return real_mask(texts, *args, **kwargs)


def test_sabotaged_masker_fails_listing_types_and_locations_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("antifaz.providers.json_walk.mask", _blind_mask)

    assert verify(_settings()) == 1

    captured = capsys.readouterr()
    assert "FAIL" in captured.out
    leaks = [line for line in captured.out.splitlines() if "LEAK" in line]
    for plant in PLANTS:
        assert any(plant.type in line for line in leaks)
    assert "system message" in captured.out
    assert "streaming" in captured.out
    _output_is_clean(captured.out + captured.err)


def test_masker_that_leaves_a_value_fails_even_when_the_guard_blocks_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The egress guard stops the request, but verify still reports the masker's fault."""

    def leaky_mask(texts: Sequence[str], *args: Any, **kwargs: Any) -> Any:
        result = real_mask(texts, *args, **kwargs)
        dni = PLANTS[0].value
        return replace(result, texts=tuple(t.replace("[[ES_DNI_1]]", dni) for t in result.texts))

    monkeypatch.setattr("antifaz.providers.json_walk.mask", leaky_mask)

    assert verify(_settings()) == 1

    captured = capsys.readouterr()
    assert "BLOCKED" in captured.out
    assert "LEAK" not in captured.out  # the guard did its job: nothing reached the provider
    _output_is_clean(captured.out + captured.err)


def test_key_echoed_by_the_provider_is_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With the gateway's key check sabotaged, the header-echo probe brings a key back."""
    monkeypatch.setattr("antifaz.api.proxy._echoes_a_key", lambda *_: False)
    monkeypatch.setattr("antifaz.api.proxy.contains_key", lambda *_: False)

    assert verify(_settings()) == 1

    captured = capsys.readouterr()
    assert "KEY" in captured.out
    assert "expected 502" in captured.out  # the gateway returned the echo instead
    _output_is_clean(captured.out + captured.err)


@pytest.mark.parametrize("route", ["openai", "anthropic"])
def test_forwarded_antifaz_key_is_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], route: str
) -> None:
    """A gateway that forwarded the client's key would hand it to the provider."""
    import antifaz.api.proxy as proxy

    real_send = proxy.send_masked

    async def forwarding(client: Any, url: str, headers: Any, *args: Any, **kw: Any) -> Any:
        return await real_send(client, url, {**headers, "x-client-key": GATEWAY_KEY}, *args, **kw)

    monkeypatch.setattr(f"antifaz.api.{route}.send_masked", forwarding)

    assert verify(_settings()) == 1

    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if "the Antifaz key reached" in line]
    assert lines
    assert all(route in line for line in lines)
    _output_is_clean(captured.out + captured.err)


def test_policy_that_allows_a_type_is_reported_as_a_note(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from antifaz.detect.types import EntityType
    from antifaz.policy import Action, Policy

    policy = Policy(entities={EntityType.EMAIL: Action.ALLOW})
    monkeypatch.setattr("antifaz.cli.verify.DEFAULT_POLICY", policy)

    assert verify(_settings()) == 0

    out = capsys.readouterr().out
    assert "EMAIL" in out and "allowed by the policy" in out


def test_answer_not_restored_is_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A gateway that forgets to restore whole answers: the values do not come back."""
    for route in ("openai", "anthropic"):
        monkeypatch.setattr(f"antifaz.api.{route}.restore_response", lambda body, _: body)

    assert verify(_settings()) == 1

    captured = capsys.readouterr()
    restore = [line for line in captured.out.splitlines() if "RESTORE" in line]
    assert any("ES_DNI" in line and "openai chat: user message" in line for line in restore)
    assert not any("streaming" in line for line in restore)  # streams still restore
    assert "LEAK" not in captured.out
    _output_is_clean(captured.out + captured.err)


def test_probe_that_never_reaches_the_provider_is_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A gateway that answers count_tokens itself: the check would prove nothing."""
    import httpx

    import antifaz.api.anthropic as route

    real_send = route._send

    async def local_count(request: Any, call: Any) -> Any:
        if call.url.endswith("/count_tokens"):
            return httpx.Response(200, json={"input_tokens": 1})
        return await real_send(request, call)

    monkeypatch.setattr(route, "_send", local_count)

    assert verify(_settings()) == 1

    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if "NO UPSTREAM" in line]
    assert len(lines) == 1 and "count_tokens" in lines[0]
    _output_is_clean(captured.out + captured.err)


def test_small_body_limit_cannot_verify_and_is_not_a_privacy_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert verify(_settings(max_body_bytes=300)) == 2

    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "could not verify" in output
    assert "ANTIFAZ_MAX_BODY_BYTES" in output
    assert "FAIL" not in output and "LEAK" not in output
    _output_is_clean(output)
