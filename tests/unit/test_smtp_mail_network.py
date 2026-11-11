"""Config-shape tests for mailnet, the only Docker network allowed to reach the mail relay.

The host's port-25 rule (deploy/smtp-egress/README.md) accepts mailnet's bridge by name and
drops every other Docker network, so only the services that join mailnet can send email.
"""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PROD_COMPOSE = REPO_ROOT / "docker-compose.prod.yml"
DEV_COMPOSE = REPO_ROOT / "docker-compose.dev.yml"
PROD_DEFAULTS = REPO_ROOT / ".env.prod.defaults"
STAGING_DEFAULTS = REPO_ROOT / ".env.staging.defaults"
PR_CHECKS = REPO_ROOT / ".github" / "workflows" / "pr-checks.yml"
README = REPO_ROOT / "deploy" / "smtp-egress" / "README.md"

MAILNET = "mailnet"
SENDERS = {"mail", "auth", "rnaseq-status-poller"}
BRIDGE_NAME_MAX = 15  # Linux interface names
# Networks on bloom-dev (checked 2026-11-10) and CI's tunnel, which mailnet must not overlap.
TAKEN = ("172.17.0.0/16", "172.18.0.0/16", "172.19.0.0/16", "172.30.250.0/28", "172.30.251.0/28", "172.30.252.0/28")


def _compose() -> dict:
    return yaml.safe_load(PROD_COMPOSE.read_text(encoding="utf-8"))


def _env_value(path: Path, key: str) -> str | None:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return None


def _settings(key: str) -> dict[str, str | None]:
    ci = re.search(rf'echo "{key}=([^"]+)" >> \.env\.ci', PR_CHECKS.read_text(encoding="utf-8"))
    assert ci, f"pr-checks.yml does not set {key} in .env.ci"
    return {
        "prod": _env_value(PROD_DEFAULTS, key),
        "staging": _env_value(STAGING_DEFAULTS, key),
        "ci": ci.group(1),
    }


def test_mailnet_is_ipv4_only_with_a_named_bridge_and_pinned_subnet():
    net = _compose()["networks"][MAILNET]
    assert net.get("enable_ipv6") is False
    assert net["driver_opts"] == {"com.docker.network.bridge.name": "${MAILNET_BRIDGE}"}
    assert net["ipam"]["config"] == [{"subnet": "${MAILNET_SUBNET}"}]


def test_only_the_senders_join_mailnet():
    joined = {name for name, svc in _compose()["services"].items() if MAILNET in (svc.get("networks") or [])}
    assert joined == SENDERS


@pytest.mark.parametrize("sender", sorted(SENDERS))
def test_senders_leave_through_mailnet(sender):
    """The highest gw_priority picks the default route, so traffic leaving bloom-dev uses mailnet."""
    networks = _compose()["services"][sender]["networks"]
    assert isinstance(networks, dict), f"{sender} must list networks as a mapping to set gw_priority"
    priorities = {name: (cfg or {}).get("gw_priority", 0) for name, cfg in networks.items()}
    assert priorities[MAILNET] > max(p for name, p in priorities.items() if name != MAILNET)


def test_mailnet_subnets_are_distinct_and_clear_of_other_networks():
    nets = {env: ipaddress.ip_network(value) for env, value in _settings("MAILNET_SUBNET").items()}
    taken = [ipaddress.ip_network(t) for t in TAKEN]
    envs = list(nets)
    for i, env in enumerate(envs):
        assert not any(nets[env].overlaps(t) for t in taken), f"{env} mailnet overlaps {TAKEN}"
        for other in envs[i + 1 :]:
            assert not nets[env].overlaps(nets[other]), f"{env} and {other} mailnet subnets overlap"


def test_bridge_names_are_valid_and_outside_dockers_br_prefix():
    names = _settings("MAILNET_BRIDGE")
    assert len(set(names.values())) == len(names), f"bridge names repeat: {names}"
    for env, name in names.items():
        assert name and len(name) <= BRIDGE_NAME_MAX, f"{env} bridge name {name!r} is not a valid interface name"
        assert not name.startswith("br-"), f"{env} bridge {name!r} would match the rule's br-+ drop"


def test_readme_accepts_the_prod_and_staging_bridges():
    """The README's commands are what goes on the host; a renamed bridge there would block its senders."""
    readme = README.read_text(encoding="utf-8")
    for env in ("prod", "staging"):
        bridge = _settings("MAILNET_BRIDGE")[env]
        assert f"-i {bridge} " in readme, f"README has no accept rule for the {env} bridge {bridge}"


def test_dev_has_no_mailnet():
    """Dev never reaches the relay, so it has no mail network to set up."""
    assert MAILNET not in (yaml.safe_load(DEV_COMPOSE.read_text(encoding="utf-8")).get("networks") or {})
