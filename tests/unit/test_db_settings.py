"""PostgreSQL settings (issue 121, PR 8b-1, ADR-0021): separate variables, never a DSN.

PostgreSQL is off by default (no ANTIFAZ_DATABASE_HOST). The password is a SecretStr: never in
a repr, a str, a dump or a message. check_database_settings() names the variable, never the
value, like check_safe_to_start(). Every value here is made up inside the test.
"""

import json
import secrets

import pytest
from pydantic import SecretStr, ValidationError

from antifaz.config import (
    Settings,
    UnsafeConfigError,
    check_database_settings,
    check_safe_to_start,
)


def canary() -> str:
    """A made-up password, different on every run, never a literal in the source."""
    return f"pg-canary-{secrets.token_hex(16)}"


def settings(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.fixture(autouse=True)
def no_antifaz_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for name in list(os.environ):
        if name.upper().startswith(("ANTIFAZ_", "PG")):
            monkeypatch.delenv(name, raising=False)


def test_postgresql_is_off_by_default() -> None:
    default = settings()

    assert default.database_host is None
    assert default.database_port == 5432
    assert default.database_name == "antifaz"
    assert default.database_user == "antifaz"
    assert default.database_password is None
    assert default.database_sslmode == "verify-full"
    assert default.database_timeout_seconds == 5.0
    check_database_settings(default)  # off is a valid choice: nothing to check


def test_the_variables_are_read_with_the_antifaz_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    password = canary()
    monkeypatch.setenv("ANTIFAZ_DATABASE_HOST", "db.internal")
    monkeypatch.setenv("ANTIFAZ_DATABASE_PORT", "6543")
    monkeypatch.setenv("ANTIFAZ_DATABASE_NAME", "keys")
    monkeypatch.setenv("ANTIFAZ_DATABASE_USER", "gateway")
    monkeypatch.setenv("ANTIFAZ_DATABASE_PASSWORD", password)
    monkeypatch.setenv("ANTIFAZ_DATABASE_SSLMODE", "disable")
    monkeypatch.setenv("ANTIFAZ_DATABASE_TIMEOUT_SECONDS", "2.5")

    read = settings()

    assert read.database_host == "db.internal"
    assert read.database_port == 6543
    assert read.database_name == "keys"
    assert read.database_user == "gateway"
    assert read.database_password is not None
    assert read.database_password.get_secret_value() == password
    assert read.database_sslmode == "disable"
    assert read.database_timeout_seconds == 2.5
    check_database_settings(read)


def test_the_libpq_variables_are_never_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PGHOST", "elsewhere.example")
    monkeypatch.setenv("PGPASSWORD", canary())
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@elsewhere.example/db")

    read = settings()

    assert read.database_host is None
    assert read.database_password is None


def test_the_password_never_shows(monkeypatch: pytest.MonkeyPatch) -> None:
    password = canary()
    configured = settings(database_host="db.internal", database_password=SecretStr(password))

    shown = [
        repr(configured),
        str(configured),
        repr(configured.model_dump()),
        configured.model_dump_json(),
        json.dumps(configured.model_dump(mode="json")),
        repr(configured.database_password),
        str(configured.database_password),
    ]

    assert not any(password in text for text in shown)


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("ANTIFAZ_DATABASE_PORT", "0"),
        ("ANTIFAZ_DATABASE_PORT", "65536"),
        ("ANTIFAZ_DATABASE_PORT", "not-a-port"),
        ("ANTIFAZ_DATABASE_SSLMODE", "require"),
        ("ANTIFAZ_DATABASE_SSLMODE", "prefer"),
        ("ANTIFAZ_DATABASE_SSLMODE", "allow"),
        ("ANTIFAZ_DATABASE_SSLMODE", "verify-ca"),
        ("ANTIFAZ_DATABASE_TIMEOUT_SECONDS", "0"),
        ("ANTIFAZ_DATABASE_TIMEOUT_SECONDS", "-1"),
        ("ANTIFAZ_DATABASE_TIMEOUT_SECONDS", "60.5"),
        ("ANTIFAZ_DATABASE_TIMEOUT_SECONDS", "nan"),
    ],
)
def test_an_invalid_value_is_refused_naming_only_the_variable(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValidationError) as caught:
        settings()

    fields = {str(error["loc"][0]) for error in caught.value.errors()}
    assert fields == {variable.removeprefix("ANTIFAZ_").lower()}
    # The input is hidden: a password pasted into the wrong variable never shows.
    assert "input_value" not in str(caught.value)


def test_a_password_in_the_wrong_variable_never_shows(monkeypatch: pytest.MonkeyPatch) -> None:
    password = canary()
    monkeypatch.setenv("ANTIFAZ_DATABASE_PORT", password)

    with pytest.raises(ValidationError) as caught:
        settings()

    assert password not in str(caught.value)
    assert password not in repr(caught.value)


def _problem(configured: Settings) -> str:
    with pytest.raises(UnsafeConfigError) as caught:
        check_database_settings(configured)
    return str(caught.value)


@pytest.mark.parametrize("password", [None, ""])
def test_a_host_without_a_password_is_refused(password: str | None) -> None:
    configured = settings(
        database_host="db.internal",
        database_password=None if password is None else SecretStr(password),
    )

    assert _problem(configured).startswith("ANTIFAZ_DATABASE_PASSWORD ")


@pytest.mark.parametrize("example", ["change-me", "change-me-database", "CHANGE_ME", "ChangeMe1"])
def test_the_example_password_is_refused(example: str) -> None:
    configured = settings(database_host="db.internal", database_password=SecretStr(example))

    message = _problem(configured)

    assert message.startswith("ANTIFAZ_DATABASE_PASSWORD ")
    assert example not in message


@pytest.mark.parametrize(
    "host",
    [
        "postgresql://user:secret@db.internal/antifaz",
        "user@db.internal",
        "db.internal/antifaz",
        "/var/run/postgresql",
        "",
        " ",
    ],
)
def test_a_host_that_is_a_url_a_dsn_or_a_socket_is_refused(host: str) -> None:
    password = canary()
    configured = settings(database_host=host, database_password=SecretStr(password))

    message = _problem(configured)

    assert message.startswith("ANTIFAZ_DATABASE_HOST ")
    assert password not in message
    if host.strip():
        assert host not in message


@pytest.mark.parametrize("field", ["database_name", "database_user"])
def test_an_empty_name_or_user_is_refused(field: str) -> None:
    configured = settings(
        database_host="db.internal", database_password=SecretStr(canary()), **{field: ""}
    )

    assert _problem(configured).startswith(f"ANTIFAZ_{field.upper()} ")


def test_a_password_without_a_host_changes_nothing() -> None:
    check_database_settings(settings(database_password=SecretStr(canary())))


def test_the_gateway_does_not_check_the_database_settings_yet() -> None:
    """8b: only `antifaz db` and doctor check them; the door (8d) will."""
    configured = settings(
        antifaz_api_key=SecretStr(secrets.token_hex(32)),
        database_host="user@db.internal",
        database_password=SecretStr("change-me"),
    )

    check_safe_to_start(configured)  # no error
