"""The e2e stack cannot reach the internet, so it can never call a real provider.

The gateway and the fake provider share a Compose network with `internal: true`; the host
reaches them only through the relay (tests/e2e/relay.py). From inside each container, the
real providers must not resolve or must not accept a TCP connection. The probe never sends a
byte (tests/e2e/image_probes.py, REACH_OUT).
"""

import json
from typing import Any

import pytest

from tests.e2e.conftest import PROJECT, Stack, docker
from tests.e2e.egress import FAKE_UPSTREAM_HOST, INTERNAL_NETWORK, REAL_PROVIDERS
from tests.e2e.image_probes import REACH_OUT

pytestmark = pytest.mark.e2e

INSIDE = ("antifaz", FAKE_UPSTREAM_HOST)


@pytest.mark.parametrize("service", INSIDE)
def test_no_container_of_the_stack_reaches_a_real_provider(stack: Stack, service: str) -> None:
    result = stack.compose(
        "exec", "-T", service, "python", "-c", REACH_OUT, *REAL_PROVIDERS, timeout=60
    )
    assert result.returncode == 0, result.stderr[-2000:]
    found = dict(line.split() for line in result.stdout.splitlines())
    assert set(found) == {target.split(":")[0] for target in REAL_PROVIDERS}
    assert "connected" not in found.values(), f"{service} reached the internet: {found}"


def test_the_gateway_and_the_fake_provider_are_only_on_the_internal_network(
    stack: Stack,
) -> None:
    network = f"{PROJECT}_{INTERNAL_NETWORK}"
    listed = docker("network", "inspect", network)
    assert listed.returncode == 0, f"no network {network}"
    assert json.loads(listed.stdout)[0]["Internal"] is True, f"{network} is not internal"
    for service in INSIDE:
        raw = docker("inspect", stack.container(service)).stdout
        networks = json.loads(raw)[0]["NetworkSettings"]["Networks"]
        assert sorted(networks) == [network], f"{service} is on {sorted(networks)}"


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
