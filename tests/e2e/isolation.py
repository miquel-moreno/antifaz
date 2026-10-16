"""Run-time checks that an e2e stack has no way out, shared by both stacks.

The stack with the fake provider (test_no_egress.py) and the stack started with the .env
written by `antifaz init` (test_image_cli.py, whose base URLs are the real providers) both run
these: the internal network is really internal, the gateway is only on it, and the canary
(REACH_OUT) cannot reach the providers by name, by literal IP (resolved on the host, so DNS
inside does not matter), public IPs, or the host side (the gateways of the Docker networks).
"""

import json
import socket
import subprocess
from collections.abc import Callable, Mapping

import pytest

from tests.e2e.conftest import SUMMARY, docker
from tests.e2e.egress import (
    HOST_PORTS,
    INTERNAL_NETWORK,
    PUBLIC_IPS,
    REAL_PROVIDERS,
    canary_problems,
    parse_reach,
    split_target,
)
from tests.e2e.image_probes import REACH_OUT

Exec = Callable[..., subprocess.CompletedProcess[str]]


def _target(address: str, port: int) -> str:
    return f"[{address}]:{port}" if ":" in address else f"{address}:{port}"


def provider_ips_from_host() -> list[str]:
    """The providers' IPs as THIS machine resolves them (up to 2 per family per name)."""
    targets: list[str] = []
    for name in REAL_PROVIDERS:
        host, port = split_target(name)
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror:
            pytest.fail(f"{host} does not resolve on the host: the canary needs its IPs")
        for family in (socket.AF_INET, socket.AF_INET6):
            addresses = sorted({str(i[4][0]) for i in infos if i[0] == family})
            targets += [_target(a, port) for a in addresses[:2]]
    return targets


def host_side_targets(networks: list[str]) -> list[str]:
    """The IPv4 gateway of each Docker network that exists, on common web and proxy ports."""
    targets: list[str] = []
    for network in networks:
        inspected = docker("network", "inspect", network)
        if inspected.returncode != 0:
            continue
        for config in json.loads(inspected.stdout)[0]["IPAM"]["Config"] or []:
            gateway = config.get("Gateway", "")
            if gateway and ":" not in gateway:
                targets += [_target(gateway, port) for port in HOST_PORTS]
    return targets


def canary_targets(project: str) -> list[str]:
    networks = [f"{project}_e2e-edge", f"{project}_{INTERNAL_NETWORK}", "bridge"]
    targets = [*REAL_PROVIDERS, *PUBLIC_IPS, *provider_ips_from_host()]
    return list(dict.fromkeys(targets + host_side_targets(networks)))


def run_canary(run: Exec, service: str, targets: list[str]) -> list[str]:
    """REACH_OUT in `service` (through `run`, a compose call); every line goes to SUMMARY."""
    result = run("exec", "-T", service, "python", "-c", REACH_OUT, *targets, timeout=300)
    assert result.returncode == 0, result.stderr[-2000:]
    found = parse_reach(result.stdout)
    for target in targets:
        seen, errno = found.get(target, ("missing", None))
        SUMMARY.append(f"e2e canary {service}: {target} {seen} {errno or ''}".rstrip())
    return canary_problems(found, targets)


def network_problems(project: str, containers: Mapping[str, str]) -> list[str]:
    """The internal network is internal, and each container is only on it."""
    network = f"{project}_{INTERNAL_NETWORK}"
    listed = docker("network", "inspect", network)
    if listed.returncode != 0:
        return [f"no network {network}"]
    problems = []
    inspected = json.loads(listed.stdout)[0]
    if inspected.get("Internal") is not True:
        problems.append(f"{network} is not internal")
    # inhibit_ipv4: the host has no address on it (an older Docker would ignore the option).
    gateways = [c.get("Gateway") for c in inspected["IPAM"]["Config"] or [] if c.get("Gateway")]
    if gateways:
        problems.append(f"{network} gives the host an address: {gateways}")
    for service, container in containers.items():
        raw = docker("inspect", container).stdout
        networks = sorted(json.loads(raw)[0]["NetworkSettings"]["Networks"])
        if networks != [network]:
            problems.append(f"{service} is on {networks}")
    return problems
