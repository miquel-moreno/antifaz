import pytest
from pydantic import SecretStr

from antifaz.api.app import create_app
from antifaz.api.gate import GateMiddleware
from antifaz.config import MIN_KEY_LENGTH, Settings, UnsafeConfigError, check_safe_to_start


def test_settings_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTIFAZ_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("ANTIFAZ_API_KEY", "test-gateway-key-not-real")
    monkeypatch.setenv("ANTIFAZ_ANTHROPIC_API_KEY", "test-provider-key-not-real")
    monkeypatch.setenv("ANTIFAZ_OPENAI_BASE_URL", "https://openai.invalid/v1")

    # _env_file is a pydantic-settings init option that mypy does not see in the signature.
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.log_level == "DEBUG"
    assert settings.app_name == "antifaz"
    assert settings.antifaz_api_key is not None
    assert settings.antifaz_api_key.get_secret_value() == "test-gateway-key-not-real"
    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "test-provider-key-not-real"
    assert settings.openai_base_url == "https://openai.invalid/v1"


def test_client_sdk_variables_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    # A shell set up for Claude Code or the SDKs must not make Antifaz call itself.
    for name, value in {
        "ANTHROPIC_API_KEY": "test-gateway-key-not-real",
        "ANTHROPIC_BASE_URL": "http://localhost:8000",
        "OPENAI_API_KEY": "test-gateway-key-not-real",
        "OPENAI_BASE_URL": "http://localhost:8000/v1",
        "LOG_LEVEL": "DEBUG",
        "MAX_BODY_BYTES": "1",
    }.items():
        monkeypatch.setenv(name, value)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.anthropic_api_key is None
    assert settings.anthropic_base_url == "https://api.anthropic.com"
    assert settings.openai_api_key is None
    assert settings.openai_base_url == "https://api.openai.com/v1"
    assert settings.log_level == "INFO"
    assert settings.max_body_bytes == 4 * 1024 * 1024


# --- Safe startup (issue 20): refuse to start instead of running open or with example keys ---

GOOD_KEY = "test-startup-key-not-real-0123456789abcdef"


def _safe(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "antifaz_api_key": SecretStr(GOOD_KEY),
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_a_safe_configuration_starts() -> None:
    check_safe_to_start(_safe(openai_api_key=SecretStr("test-provider-key-not-real")))


@pytest.mark.parametrize(
    ("key", "reason"),
    [
        (None, "missing"),
        ("", "missing"),
        ("short-test-key-not-real", "at least 32"),
        ("x" * (MIN_KEY_LENGTH - 1), "at least 32"),
        ("change-me-to-a-long-random-value-0123456789", "example value"),
        ("change_me_to_a_long_random_value_0123456789", "example value"),
        ("ChangeMe-to-a-long-random-value-0123456789ab", "example value"),
        ("ab" * 20, "different characters"),
        ("0123456" * 6, "different characters"),
        ("CHANGE-ME-to-a-long-random-value-0123456789", "example value"),
        ("test key with spaces not real 0123456789abc", "printable ASCII"),
        ("test-key-not-real-ñ-0123456789abcdefghijklm", "printable ASCII"),
        ("test-key-not-real-\t-0123456789abcdefghijklm", "printable ASCII"),
    ],
)
def test_unsafe_gateway_key_refuses_to_start(key: str | None, reason: str) -> None:
    settings = _safe(antifaz_api_key=None if key is None else SecretStr(key))

    with pytest.raises(UnsafeConfigError) as info:
        create_app(settings)

    message = str(info.value)
    assert reason in message
    assert "ANTIFAZ_API_KEY" in message
    if key:
        assert key not in message
    assert info.value.__cause__ is None


def test_a_key_of_exactly_the_minimum_length_starts() -> None:
    fake = "test-minimum-key-not-real-012345"
    assert len(fake) == MIN_KEY_LENGTH
    check_safe_to_start(_safe(antifaz_api_key=SecretStr(fake)))


@pytest.mark.parametrize(
    ("field", "variable"),
    [
        ("openai_api_key", "ANTIFAZ_OPENAI_API_KEY"),
        ("anthropic_api_key", "ANTIFAZ_ANTHROPIC_API_KEY"),
    ],
)
def test_provider_example_key_refuses_to_start(field: str, variable: str) -> None:
    value = "change-me-provider-key"

    with pytest.raises(UnsafeConfigError) as info:
        check_safe_to_start(_safe(**{field: SecretStr(value)}))

    assert variable in str(info.value)
    assert value not in str(info.value)


def test_provider_keys_are_optional_to_start() -> None:
    # A missing provider key only disables that route (503), it does not open anything.
    check_safe_to_start(_safe(openai_api_key=None, anthropic_api_key=None))


@pytest.mark.parametrize("hosts", [[], ["*"], ["localhost", "*"], ["", "localhost"]])
def test_open_allowed_hosts_refuse_to_start(hosts: list[str]) -> None:
    with pytest.raises(UnsafeConfigError) as info:
        check_safe_to_start(_safe(allowed_hosts=hosts))

    assert "ANTIFAZ_ALLOWED_HOSTS" in str(info.value)


@pytest.mark.parametrize("origins", [["*"], ["null"], ["https://ok.example", "*"], [""]])
def test_open_allowed_origins_refuse_to_start(origins: list[str]) -> None:
    with pytest.raises(UnsafeConfigError) as info:
        check_safe_to_start(_safe(allowed_origins=origins))

    assert "ANTIFAZ_ALLOWED_ORIGINS" in str(info.value)


def test_default_hosts_and_origins_are_closed() -> None:
    settings = _safe()

    assert settings.allowed_hosts == ["localhost", "127.0.0.1", "[::1]"]
    assert settings.allowed_origins == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("antifaz.internal", ["antifaz.internal"]),
        ("antifaz.internal, localhost", ["antifaz.internal", "localhost"]),
        ('["antifaz.internal", "localhost"]', ["antifaz.internal", "localhost"]),
    ],
)
def test_lists_are_read_from_environment(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: list[str]
) -> None:
    monkeypatch.setenv("ANTIFAZ_ALLOWED_HOSTS", raw)
    monkeypatch.setenv("ANTIFAZ_ALLOWED_ORIGINS", raw.replace("antifaz.internal", "https://a.b"))

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.allowed_hosts == expected
    assert settings.allowed_origins == [
        v.replace("antifaz.internal", "https://a.b") for v in expected
    ]


def test_module_import_does_not_build_the_app() -> None:
    # The app is built by the server (`uvicorn --factory`), never at import time: importing
    # must not need a key, and a refused start must not happen as an import side effect.
    import antifaz.api.app as module

    assert not hasattr(module, "app")


def test_an_ipv6_host_first_is_not_read_as_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTIFAZ_ALLOWED_HOSTS", "[::1],localhost")
    monkeypatch.setenv("ANTIFAZ_ALLOWED_ORIGINS", "")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.allowed_hosts == ["[::1]", "localhost"]
    assert settings.allowed_origins == []


@pytest.mark.parametrize(
    "value",
    [
        "",
        "change-me",
        "Change_Me-provider-key",
        "CHANGEME-provider-key",
        "test provider key not real",
        "test-provider-key-\u00f1-not-real",
        "test-provider-key\n-not-real",
    ],
)
@pytest.mark.parametrize(
    ("field", "variable"),
    [
        ("openai_api_key", "ANTIFAZ_OPENAI_API_KEY"),
        ("anthropic_api_key", "ANTIFAZ_ANTHROPIC_API_KEY"),
    ],
)
def test_unsafe_provider_key_refuses_to_start(value: str, field: str, variable: str) -> None:
    with pytest.raises(UnsafeConfigError) as info:
        check_safe_to_start(_safe(**{field: SecretStr(value)}))

    assert variable in str(info.value)
    if value:
        assert value not in str(info.value)


@pytest.mark.parametrize("hosts", [["*.com"], ["*.example"], ["a*.example.com"], ["x.*.com"]])
def test_broad_host_wildcards_refuse_to_start(hosts: list[str]) -> None:
    with pytest.raises(UnsafeConfigError) as info:
        check_safe_to_start(_safe(allowed_hosts=hosts))

    assert "ANTIFAZ_ALLOWED_HOSTS" in str(info.value)


def test_subdomain_wildcard_with_two_labels_starts() -> None:
    check_safe_to_start(_safe(allowed_hosts=["*.example.com", "antifaz"]))


def test_allowed_hosts_are_lower_cased() -> None:
    assert _safe(allowed_hosts=["Antifaz.Example.COM"]).allowed_hosts == ["antifaz.example.com"]


@pytest.mark.parametrize(
    "origin",
    [
        "https://intranet.example/",
        "https://intranet.example/app",
        "https://intranet.example?x=1",
        "intranet.example",
        "ftp://intranet.example",
        "https://",
    ],
)
def test_origins_with_path_or_without_scheme_refuse_to_start(origin: str) -> None:
    with pytest.raises(UnsafeConfigError) as info:
        check_safe_to_start(_safe(allowed_origins=[origin]))

    assert "ANTIFAZ_ALLOWED_ORIGINS" in str(info.value)
    assert "scheme://host" in str(info.value)


def test_exact_origins_start() -> None:
    check_safe_to_start(
        _safe(allowed_origins=["https://intranet.example", "http://localhost:3000"])
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('[ "a.example" , "b.example" ]', ["a.example", "b.example"]),
        ("[]", []),
        ("[ ]", []),
        ("[::1]", ["[::1]"]),
    ],
)
def test_json_lists_with_spaces_are_read(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: list[str]
) -> None:
    monkeypatch.setenv("ANTIFAZ_ALLOWED_HOSTS", raw)

    assert Settings(_env_file=None).allowed_hosts == expected  # type: ignore[call-arg]


@pytest.mark.parametrize("key", ["", "short-key", "a" * 31])
def test_gate_refuses_to_be_built_with_a_weak_key(key: str) -> None:
    # The middleware fails closed on its own, even if someone skips check_safe_to_start().
    async def app(scope: object, receive: object, send: object) -> None:  # pragma: no cover
        return None

    with pytest.raises(ValueError, match="key"):
        GateMiddleware(app, api_key=key, allowed_origins=[])  # type: ignore[arg-type]
