"""Kong must reach Realtime by a hostname whose first label is the tenant name.

Realtime picks the tenant from the first DNS label of the Host header, and the
self-host seed creates one tenant, named by SELF_HOST_TENANT_NAME (default
`realtime-dev`). Upstream `http://realtime:4000` makes every socket fail with
`TenantNotFound: realtime`, so the upstream host must both match the tenant and
resolve on supanet via an alias on the realtime service.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
KONG_CONFIG = REPO_ROOT / "volumes" / "api" / "kong.yml"
COMPOSE_FILES = ("docker-compose.dev.yml", "docker-compose.prod.yml")
REALTIME_SERVICES = ("realtime-v1-ws", "realtime-v1-rest")
SEED_DEFAULT_TENANT = "realtime-dev"


def _kong_upstream_hosts() -> dict[str, str]:
    config = yaml.safe_load(KONG_CONFIG.read_text(encoding="utf-8"))
    services = {s["name"]: s for s in config["services"]}
    return {
        name: urlparse(services[name]["url"]).hostname for name in REALTIME_SERVICES
    }


def _realtime_service(compose_file: str) -> dict:
    compose = yaml.safe_load((REPO_ROOT / compose_file).read_text(encoding="utf-8"))
    return compose["services"]["realtime"]


def _tenant_name(service: dict) -> str:
    env = service.get("environment") or {}
    if isinstance(env, list):
        env = dict(item.partition("=")[::2] for item in env)
    return str(env.get("SELF_HOST_TENANT_NAME", SEED_DEFAULT_TENANT))


def _supanet_aliases(service: dict) -> list[str]:
    networks = service.get("networks") or {}
    if isinstance(networks, list):
        return []
    return list((networks.get("supanet") or {}).get("aliases") or [])


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
@pytest.mark.parametrize("kong_service", REALTIME_SERVICES)
def test_kong_upstream_first_label_is_the_tenant(compose_file, kong_service):
    host = _kong_upstream_hosts()[kong_service]
    tenant = _tenant_name(_realtime_service(compose_file))
    assert host.split(".")[0] == tenant, (
        f"{kong_service} forwards Host {host!r}; Realtime would look up tenant "
        f"{host.split('.')[0]!r}, but the seeded tenant is {tenant!r}"
    )


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
@pytest.mark.parametrize("kong_service", REALTIME_SERVICES)
def test_kong_upstream_host_resolves_on_supanet(compose_file, kong_service):
    host = _kong_upstream_hosts()[kong_service]
    aliases = _supanet_aliases(_realtime_service(compose_file))
    assert host in aliases, (
        f"{kong_service} targets {host!r}, which is not a supanet alias of the "
        f"realtime service in {compose_file} (aliases: {aliases})"
    )
