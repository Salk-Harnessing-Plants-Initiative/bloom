"""Only Bloom's mail senders may reach the relay: the compose side of the port-25 rule.

The host rule (deploy/smtp-egress) names the senders by address, so each environment's
internal network has a pinned range, the three senders have fixed addresses outside the
automatic range, and no container keeps a capability that could forge another's address.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
COMPOSE = yaml.safe_load((REPO / "docker-compose.prod.yml").read_text())
SENDERS = {
    "mail": "MAIL_IP",
    "auth": "AUTH_IP",
    "rnaseq-status-poller": "RNASEQ_POLLER_IP",
}


def _env(name: str) -> dict[str, str]:
    values = {}
    for line in (REPO / name).read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def test_no_service_can_forge_an_address():
    """NET_RAW forges another container's address; every service drops it (or all)."""
    missing = [
        n
        for n, s in COMPOSE["services"].items()
        if not {"ALL", "NET_RAW"} & set(s.get("cap_drop") or [])
    ]
    assert missing == [], (
        f"these keep default capabilities (NET_RAW can forge addresses): {missing}"
    )


def test_the_internal_network_has_a_pinned_range():
    [config] = COMPOSE["networks"]["supanet"]["ipam"]["config"]
    assert config == {
        "subnet": "${SUPANET_SUBNET}",
        "ip_range": "${SUPANET_AUTO_RANGE}",
    }


def test_only_the_senders_have_fixed_addresses():
    fixed = {}
    for name, service in COMPOSE["services"].items():
        nets = service.get("networks")
        supanet = nets.get("supanet") if isinstance(nets, dict) else None
        if isinstance(supanet, dict) and supanet.get("ipv4_address"):
            fixed[name] = supanet["ipv4_address"]
    assert fixed == {name: f"${{{var}}}" for name, var in SENDERS.items()}


@pytest.mark.parametrize("env_file", [".env.prod.defaults", ".env.staging.defaults"])
def test_sender_addresses_sit_in_the_range_but_outside_the_automatic_part(env_file):
    env = _env(env_file)
    subnet = ipaddress.ip_network(env["SUPANET_SUBNET"])
    auto = ipaddress.ip_network(env["SUPANET_AUTO_RANGE"])
    assert auto.subnet_of(subnet)
    addresses = [ipaddress.ip_address(env[var]) for var in SENDERS.values()]
    assert len(set(addresses)) == 3
    for address in addresses:
        assert address in subnet and address not in auto


def test_prod_and_staging_ranges_do_not_overlap_each_other_or_the_tunnels():
    prod, staging = _env(".env.prod.defaults"), _env(".env.staging.defaults")
    nets = [
        ipaddress.ip_network(prod["SUPANET_SUBNET"]),
        ipaddress.ip_network(staging["SUPANET_SUBNET"]),
        ipaddress.ip_network(prod["TUNNEL_SUBNET"]),
        ipaddress.ip_network(staging["TUNNEL_SUBNET"]),
    ]
    for i, a in enumerate(nets):
        for b in nets[i + 1 :]:
            assert not a.overlaps(b), (a, b)
