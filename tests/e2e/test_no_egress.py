"""The e2e stack cannot reach the internet, so it can never call a real provider.

The gateway and the fake provider share a Compose network with `internal: true`; the host
reaches them only through the relay (tests/e2e/relay.py). From inside each container, nothing
outside may accept a TCP connection: the providers by name and by the IPs the host resolves
for them, public IPs (IPv4 and IPv6) and the host side (the gateways of the Docker networks
on web and proxy ports). Every result is judged against an allow-list and printed at the end
of the run. The probe never sends a byte (tests/e2e/image_probes.py, REACH_OUT).
"""

import json
from typing import Any

import pytest

from tests.e2e.conftest import PROJECT, Stack, docker
from tests.e2e.egress import FAKE_UPSTREAM_HOST, INTERNAL_NETWORK
from tests.e2e.image_probes import EFFECTIVE_UPSTREAMS
from tests.e2e.isolation import canary_targets, network_problems, run_canary

pytestmark = pytest.mark.e2e

INSIDE = ("antifaz", FAKE_UPSTREAM_HOST)


@pytest.fixture(scope="module")
def targets(stack: Stack) -> list[str]:
    return canary_targets(PROJECT)


@pytest.mark.parametrize("service", INSIDE)
def test_no_container_of_the_stack_reaches_anything_outside(
    stack: Stack, targets: list[str], service: str
) -> None:
    assert run_canary(stack.compose, service, targets) == []


def test_the_gateway_and_the_fake_provider_are_only_on_the_internal_network(
    stack: Stack,
) -> None:
    containers = {service: stack.container(service) for service in INSIDE}
    assert network_problems(PROJECT, containers) == []


def test_the_gateway_really_uses_the_fake_provider(stack: Stack) -> None:
    """Settings() inside the running gateway, printed as booleans only (never a value)."""
    run = ("exec", "-T", "antifaz", "python", "-c", EFFECTIVE_UPSTREAMS)
    result = stack.compose(*run)
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.split() == ["True"] * 6
    # The probe can tell: the same check with a real provider's URL says False.
    wrong = ("-e", "ANTIFAZ_OPENAI_BASE_URL=https://api.openai.com/v1")
    result = stack.compose(*run[:2], *wrong, *run[2:])
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.split() == ["False", "False", "False"] + ["True"] * 3


def _inspect(stack: Stack, service: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(docker("inspect", stack.container(service)).stdout)[0]
    return data


def test_the_gateway_and_the_fake_provider_publish_no_port_themselves(stack: Stack) -> None:
    """docker-compose.yml still declares the gateway's port, but Docker never binds it on an
    internal network: only the relay is reachable from the host."""
    for service in INSIDE:
        bound = _inspect(stack, service)["NetworkSettings"]["Ports"] or {}
        assert not any(bound.values()), f"{service} publishes a port itself: {bound}"


def test_the_relay_is_the_only_door_and_listens_on_127_0_0_1_only(stack: Stack) -> None:
    relay = _inspect(stack, "relay")
    networks = sorted(relay["NetworkSettings"]["Networks"])
    assert networks == [f"{PROJECT}_e2e-edge", f"{PROJECT}_{INTERNAL_NETWORK}"]
    bound = relay["NetworkSettings"]["Ports"]
    assert sorted(bound) == ["8000/tcp", "9000/tcp"]
    assert {b["HostIp"] for port in bound.values() for b in port} == {"127.0.0.1"}
    host = relay["HostConfig"]
    assert host["ReadonlyRootfs"] is True
    assert host["CapDrop"] == ["ALL"]
    assert "no-new-privileges:true" in host["SecurityOpt"]
