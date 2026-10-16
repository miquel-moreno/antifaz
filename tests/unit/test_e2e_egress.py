"""The e2e tests can never reach a real provider (tests/e2e/egress.py).

The guard reads the throwaway env file the e2e fixture writes: if a provider base URL could
reach a real provider (missing, another host, https, a user part that hides the real host...),
the session stops before Compose starts. And, statically: every `docker run` in tests/e2e uses
`--network none`, and in the Compose files only the relay is on a network with a way out.
"""

from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.e2e.egress import (
    INTERNAL_NETWORK,
    UPSTREAM_VARS,
    docker_run_problems,
    module_lists,
    read_env,
    run_problems,
    upstream_problems,
)

REPO = Path(__file__).resolve().parents[2]
E2E = REPO / "tests" / "e2e"

FAKE_OPENAI = "http://fake-upstream:9000/v1"
FAKE_ANTHROPIC = "http://fake-upstream:9000"


def _env(
    openai: str | None = FAKE_OPENAI, anthropic: str | None = FAKE_ANTHROPIC
) -> dict[str, str]:
    env = {"ANTIFAZ_LOG_LEVEL": "INFO"}
    if openai is not None:
        env["ANTIFAZ_OPENAI_BASE_URL"] = openai
    if anthropic is not None:
        env["ANTIFAZ_ANTHROPIC_BASE_URL"] = anthropic
    return env


def test_both_urls_on_the_fake_provider_pass() -> None:
    assert upstream_problems(_env()) == []


@pytest.mark.parametrize(
    "url",
    [
        "http://fake-upstream:9000",
        "http://fake-upstream:9000/",
        "http://fake-upstream:9000/v1",
        "http://FAKE-UPSTREAM:9000/v1",
    ],
)
def test_any_path_on_the_fake_provider_passes(url: str) -> None:
    assert upstream_problems(_env(openai=url, anthropic=url)) == []


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://api.openai.com/v1",
        "https://api.anthropic.com",
        "http://api.openai.com/v1",
        "https://fake-upstream:9000/v1",  # https: not the fake provider
        "http://fake-upstream/v1",  # port 80
        "http://fake-upstream:443/v1",
        "http://fake-upstream.example.com:9000/v1",
        "http://fake-upstream:9000@api.openai.com/v1",  # the real host hidden behind a user
        "http://user@fake-upstream:9000/v1",
        "http://api.openai.com/fake-upstream:9000",
        "fake-upstream:9000",
        "http://fake-upstream:notaport",
        " http://fake-upstream:9000",
        "http://fake-up\tstream:9000",  # urlsplit drops tabs: it would read fake-upstream
        "http://fake-upstream:9000/v1\n",
        "http://fake-upstream:9000/v\r1",
        "http://fake-upstream:9000/v 1",
        "http://fäke-upstream:9000",
        "http://fake-upstream:9000#@api.openai.com",
        "http://fake-upstream:9000/v1#x",
        "http://fake-upstream:9000/v1?x=1",
        "http://fake-upstream:9000?",
        "http://fake-upstream:09000",
        "http://fake-upstream:9000:9000",
        "http://[::1]:9000",
    ],
)
def test_a_url_that_is_not_the_fake_provider_is_refused(url: str) -> None:
    problems = upstream_problems(_env(openai=url))
    assert len(problems) == 1
    assert "ANTIFAZ_OPENAI_BASE_URL" in problems[0]
    if url.strip():
        assert url.strip() not in problems[0]  # the variable is named, never its value


def test_a_missing_url_is_refused_because_the_default_is_the_real_provider() -> None:
    problems = upstream_problems(_env(openai=None, anthropic=None))
    assert len(problems) == 2
    for name, problem in zip(UPSTREAM_VARS, problems, strict=True):
        assert name in problem


def test_each_bad_url_gives_its_own_message() -> None:
    problems = upstream_problems(_env(anthropic="https://api.anthropic.com"))
    assert len(problems) == 1
    assert "ANTIFAZ_ANTHROPIC_BASE_URL" in problems[0]


def test_read_env_reads_name_value_lines() -> None:
    text = (
        "# comment\n\nANTIFAZ_API_KEY=abc\nANTIFAZ_OPENAI_BASE_URL=http://fake-upstream:9000/v1\n"
    )
    assert read_env(text) == {
        "ANTIFAZ_API_KEY": "abc",
        "ANTIFAZ_OPENAI_BASE_URL": "http://fake-upstream:9000/v1",
    }


def test_read_env_keeps_the_last_value_and_an_equals_sign_in_the_value() -> None:
    text = "A=1\nA=2\nB=x=y\nnot a line\n"
    assert read_env(text) == {"A": "2", "B": "x=y"}


def test_the_guard_reads_the_file_the_e2e_fixture_writes() -> None:
    """The real fixture's lines: what conftest.py writes passes the guard."""
    from tests.e2e import conftest

    text = conftest.env_text({"gateway": "a", "openai": "b", "anthropic": "c"})
    assert upstream_problems(read_env(text)) == []


# --- Every docker run in tests/e2e has no network ---------------------------------------------


def _known_lists() -> dict[str, Any]:
    return module_lists((E2E / "image_probes.py").read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", sorted(E2E.glob("*.py")), ids=lambda p: p.name)
def test_every_docker_run_in_the_e2e_tests_has_no_network(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    assert docker_run_problems(source, _known_lists()) == []


@pytest.mark.parametrize(
    "source",
    [
        'docker("run", "--rm", "--entrypoint", "python", IMAGE, "-m", "pip")',
        'docker("run", "--rm", "--network", "bridge", IMAGE)',
        'docker("run", "--rm", "--network", "host", IMAGE)',
        'docker("run", "--rm", "--network", name, IMAGE)',
        'docker("run", "--rm", "--network", "none", "--network", "bridge", IMAGE)',
        'docker("run", "--rm", "--network=none", "--network=bridge", IMAGE)',
        'docker("run", "--rm", "--net", "none", IMAGE)',
        'docker("run", "--rm", IMAGE, "--help", "--network", "none")',
        'docker("run", "--privileged", IMAGE, "--network", "none")',
        'docker("run", "-P", IMAGE, "--network", "none")',
        'docker("run", "--network", "none", "-p", "127.0.0.1:1:8000", IMAGE)',
        'docker("run", "--network", "none", "--privileged", IMAGE)',
        'docker("run", "--network", "none", "--cap-add", "NET_ADMIN", IMAGE)',
        'docker("run", "--network", "none", "--some-new-flag", IMAGE)',
        'docker("container", "run", "--rm", IMAGE)',
        'docker("container", "create", IMAGE)',
        'docker("create", IMAGE)',
        'command = ["docker", "run", "-d", "-p", "127.0.0.1:1:8000", IMAGE]',
        'command = ["run", "-d", "--name", name, IMAGE]',
        'command = ["docker", "run", "--network", "none", *options, IMAGE]',
        'OPTS = ["--read-only"]\ndocker("run", "--rm", *OPTS, IMAGE)',
        'docker("run", "--rm", *options, "--network", "none", IMAGE)',
        'subprocess.run(["docker", "compose", "-f", "x.yml", "run", "antifaz"])',
        'stack.compose("run", "--rm", "antifaz")',
        'compose("run", "antifaz", "doctor")',
        'subprocess.run("docker run --rm antifaz:local", shell=True)',
        "subprocess.run(command, shell=True)",
        'os.system("docker run --rm antifaz:local")',
        'SCRIPT = "docker create antifaz:local"',
        'x = f"docker container run {IMAGE}"',
        'x = "docker compose run antifaz"',
    ],
)
def test_the_check_finds_a_container_started_with_a_network(source: str) -> None:
    assert docker_run_problems(source) != []


@pytest.mark.parametrize(
    "source",
    [
        'docker("run", "--rm", "--network", "none", IMAGE, "--help")',
        'docker("run", "--rm", "--network=none", IMAGE)',
        'docker("run", "--rm", "--network", "none", IMAGE, "--network", "bridge")',  # the command's
        'HARDENED = ["--read-only"]\nHARDENED += ["--network", "none"]\n'
        'docker("run", "--rm", *HARDENED, IMAGE)',
        # After --network none, a list this check cannot read is checked at run time by docker().
        'docker("run", "--rm", "--network", "none", *options, IMAGE)',
        'command = ["docker", "run", "-d", "--network", "none", IMAGE]',
        'docker("exec", name, "python", "-c", "x")',
        'docker("image", "inspect", IMAGE)',
        "docker(*command)",
        'stack.compose("exec", "-T", "antifaz", "python")',
        'compose("up", "-d", "--wait")',
        "subprocess.run(command, shell=False)",
        '"""A docstring may say `docker run --rm IMAGE`."""',
        'def f():\n    """`docker run` the image."""',
    ],
)
def test_the_check_lets_a_run_without_network_and_other_commands_through(source: str) -> None:
    assert docker_run_problems(source) == []


def test_the_check_resolves_lists_imported_from_another_module() -> None:
    source = 'docker("run", "--rm", *HARDENED, IMAGE)'
    assert docker_run_problems(source) != []
    assert docker_run_problems(source, _known_lists()) == []


def test_the_check_finds_the_run_this_pr_fixed() -> None:
    """test_compose.py before this change: `python -m pip` ran with the default network."""
    source = 'found = docker("run", "--rm", "--entrypoint", "python", IMAGE, "-m", module)'
    assert len(docker_run_problems(source)) == 1


@pytest.mark.parametrize(
    "argv",
    [
        ("run", "--rm", "antifaz:local"),
        ("run", "--rm", "--network", "bridge", "antifaz:local"),
        ("run", "--network", "none", "--network", "host", "antifaz:local"),
        ("run", "--network", "none", "-p", "127.0.0.1:1:8000", "antifaz:local"),
        ("create", "antifaz:local"),
        ("container", "run", "antifaz:local"),
        ("compose", "-f", "x.yml", "run", "antifaz"),
    ],
)
def test_the_docker_helper_refuses_a_run_with_a_network_before_calling_docker(
    argv: tuple[str, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run-time half: the e2e `docker()` helper checks the real argument list."""
    import subprocess

    from tests.e2e import conftest

    def never(*args: object, **kwargs: object) -> None:
        raise AssertionError("docker was called")

    monkeypatch.setattr(subprocess, "run", never)
    with pytest.raises(AssertionError, match="refused"):
        conftest.docker(*argv)


def test_the_docker_helper_lets_a_run_without_network_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    from tests.e2e import conftest

    calls: list[object] = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))
    conftest.docker("run", "--rm", "--network", "none", "-e", "X", "antifaz:local", "--help")
    conftest.docker("inspect", "x")
    assert len(calls) == 2


@pytest.mark.parametrize(
    "argv",
    [
        ["run", "--rm", "--network", "none", "antifaz:local"],
        ["docker", "run", "--network=none", "-d", "--name", "x", "antifaz:local", "-p", "1"],
        ["exec", "x", "python"],
        ["compose", "up", "-d"],
    ],
)
def test_run_problems_accepts_real_argument_lists(argv: list[str]) -> None:
    assert run_problems(argv) == []


# --- Only the relay has a way out in the e2e Compose files ------------------------------------


def _compose(name: str) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((E2E / name).read_text(encoding="utf-8"))
    return data


def test_the_internal_network_has_no_way_out() -> None:
    networks = _compose("docker-compose.no-egress.yml")["networks"]
    internal = networks[INTERNAL_NETWORK]
    assert internal["internal"] is True
    # A fixed subnet, so the relay's targets are fixed IPs inside it (no name lookups).
    assert internal["ipam"] == {
        "config": [
            {"subnet": "${ANTIFAZ_E2E_SUBNET:?set by the e2e tests}",
             "ip_range": "${ANTIFAZ_E2E_IP_RANGE:?set by the e2e tests}"}
        ]
    }  # fmt: skip


def test_the_gateway_and_the_fake_provider_are_only_on_the_internal_network() -> None:
    gateway = _compose("docker-compose.no-egress.yml")["services"]["antifaz"]
    fake = _compose("docker-compose.e2e.yml")["services"]["fake-upstream"]
    assert gateway["networks"] == {
        INTERNAL_NETWORK: {"ipv4_address": "${ANTIFAZ_E2E_GATEWAY_IP:?set by the e2e tests}"}
    }
    assert fake["networks"] == {
        INTERNAL_NETWORK: {"ipv4_address": "${ANTIFAZ_E2E_FAKE_IP:?set by the e2e tests}"}
    }
    assert "ports" not in fake


def test_the_relay_routes_to_fixed_ips_inside_the_subnet() -> None:
    base = _compose("docker-compose.no-egress.yml")["services"]["relay"]["environment"]
    e2e = _compose("docker-compose.e2e.yml")["services"]["relay"]["environment"]
    assert base["E2E_RELAY_SUBNET"] == "${ANTIFAZ_E2E_SUBNET:?set by the e2e tests}"
    assert base["E2E_RELAY_ROUTES"] == "8000=${ANTIFAZ_E2E_GATEWAY_IP}:8000"
    assert e2e["E2E_RELAY_ROUTES"] == (
        "8000=${ANTIFAZ_E2E_GATEWAY_IP}:8000,9000=${ANTIFAZ_E2E_FAKE_IP}:9000"
    )


@pytest.mark.parametrize("which", ["stack", "init"])
def test_the_internal_addresses_are_consistent(which: str) -> None:
    import ipaddress

    from tests.e2e.egress import internal_addresses

    env = internal_addresses(which)
    subnet = ipaddress.ip_network(env["ANTIFAZ_E2E_SUBNET"])
    ip_range = ipaddress.ip_network(env["ANTIFAZ_E2E_IP_RANGE"])
    fixed = [
        ipaddress.ip_address(env[k]) for k in ("ANTIFAZ_E2E_GATEWAY_IP", "ANTIFAZ_E2E_FAKE_IP")
    ]
    assert ip_range.subnet_of(subnet)  # type: ignore[arg-type]
    for address in fixed:
        assert address in subnet and address not in ip_range  # dynamic IPs never collide


def test_the_two_stacks_use_different_subnets() -> None:
    """The init stack starts while the main one runs: their subnets must not overlap."""
    import ipaddress

    from tests.e2e.egress import internal_addresses

    one, two = (
        ipaddress.ip_network(internal_addresses(w)["ANTIFAZ_E2E_SUBNET"]) for w in ("stack", "init")
    )
    assert not one.overlaps(two)


def test_the_relay_publishes_on_127_0_0_1_only_and_keeps_the_hardening() -> None:
    relays = [
        _compose(name)["services"]["relay"]
        for name in ("docker-compose.no-egress.yml", "docker-compose.e2e.yml")
    ]
    ports = [port for relay in relays for port in relay["ports"]]
    assert len(ports) == 2
    assert all(port.startswith("127.0.0.1:") for port in ports)
    relay = relays[0]
    assert relay["read_only"] is True
    assert relay["cap_drop"] == ["ALL"]
    assert relay["security_opt"] == ["no-new-privileges:true"]
    assert relay["cpus"] == 0.5
    assert relay["pids_limit"] > 0 and relay["mem_limit"]
    assert sorted(relay["networks"]) == ["e2e-edge", INTERNAL_NETWORK]


def test_the_e2e_fixtures_use_the_no_egress_file() -> None:
    from tests.e2e import conftest

    no_egress = E2E / "docker-compose.no-egress.yml"
    assert no_egress in conftest.NO_EGRESS_FILES
    assert conftest.COMPOSE_FILES[: len(conftest.NO_EGRESS_FILES)] == conftest.NO_EGRESS_FILES
    cli = (E2E / "test_image_cli.py").read_text(encoding="utf-8")
    assert "BASE_FILES" not in cli
    assert "NO_EGRESS_FILES" in cli
