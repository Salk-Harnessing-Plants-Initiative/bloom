"""Live assertion: the mail senders' traffic leaving the host goes out through mailnet.

The host's port-25 rule (deploy/smtp-egress/README.md) accepts only mailnet's bridge, so a
sender whose default route stayed on supanet would be cut off. Each container's default route
is read from /proc/net/route.

Config-shape counterpart: tests/unit/test_smtp_mail_network.py.
"""

from __future__ import annotations

import ipaddress
import json
import os
import shutil
import socket
import struct
import subprocess

import pytest

pytestmark = pytest.mark.integration

IN_CI = os.environ.get("GITHUB_ACTIONS") == "true"
SENDERS = ("mail", "auth", "rnaseq-status-poller")
NOT_A_SENDER = "bloommcp"
DEFAULT_DESTINATION = "00000000"


def _unavailable(reason: str):
    """Fail in CI, where the stack is always up; skip on a laptop without it."""
    if IN_CI:
        pytest.fail(reason)
    pytest.skip(reason)


def _docker(*args: str) -> str:
    if shutil.which("docker") is None:
        _unavailable("docker is not installed")
    result = subprocess.run(["docker", *args], capture_output=True, text=True, check=False, timeout=30)
    if result.returncode != 0:
        _unavailable(f"docker {args[0]} failed: {result.stderr.strip()}")
    return result.stdout


def _container(service: str) -> str:
    ids = _docker("ps", "-q", "--filter", f"label=com.docker.compose.service={service}").split()
    if len(ids) != 1:
        _unavailable(f"expected one running {service} container, found {len(ids)}")
    return ids[0]


@pytest.fixture(scope="module")
def mailnet_gateway() -> ipaddress.IPv4Address:
    ids = _docker("network", "ls", "-q", "--filter", "label=com.docker.compose.network=mailnet").split()
    if len(ids) != 1:
        _unavailable(f"expected one mailnet network, found {len(ids)}")
    config = json.loads(_docker("network", "inspect", "-f", "{{json .IPAM.Config}}", ids[0]))
    return ipaddress.IPv4Address(config[0]["Gateway"])


def _default_gateway(service: str) -> ipaddress.IPv4Address:
    table = _docker("exec", _container(service), "cat", "/proc/net/route")
    for line in table.splitlines()[1:]:
        fields = line.split()
        if fields[1] == DEFAULT_DESTINATION:
            # /proc/net/route prints addresses as little-endian hex.
            return ipaddress.IPv4Address(socket.inet_ntoa(struct.pack("<L", int(fields[2], 16))))
    pytest.fail(f"{service} has no default route")


@pytest.mark.parametrize("service", SENDERS)
def test_sender_default_route_is_mailnet(service, mailnet_gateway):
    assert _default_gateway(service) == mailnet_gateway


def test_other_containers_do_not_route_through_mailnet(mailnet_gateway):
    assert _default_gateway(NOT_A_SENDER) != mailnet_gateway
