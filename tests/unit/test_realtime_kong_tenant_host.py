"""Kong must reach Realtime by a hostname whose first label is the tenant name.

Realtime picks the tenant from the first DNS label of the Host header, and the
self-host seed creates one tenant, named by SELF_HOST_TENANT_NAME (default
`realtime-dev`). Upstream `http://realtime:4000` makes every socket fail with
`TenantNotFound: realtime`, so the upstream host must both match the tenant and
resolve on supanet via an alias on the realtime service.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
KONG_CONFIG = REPO_ROOT / "volumes" / "api" / "kong.yml"
COMPOSE_FILES = ("docker-compose.dev.yml", "docker-compose.prod.yml")
REALTIME_SERVICES = ("realtime-v1-ws", "realtime-v1-rest")
REALTIME_REST_SERVICE = "realtime-v1-rest"
SEED_DEFAULT_TENANT = "realtime-dev"
# Compose's `${VAR:-default}` / `${VAR-default}` substitution.
COMPOSE_DEFAULT = re.compile(r"^\$\{[A-Z0-9_]+:?-(?P<default>[^}]*)\}$")


def _kong_services() -> dict[str, dict]:
    config = yaml.safe_load(KONG_CONFIG.read_text(encoding="utf-8"))
    return {s["name"]: s for s in config["services"]}


def _kong_upstream_hosts() -> dict[str, str]:
    services = _kong_services()
    return {
        name: urlparse(services[name]["url"]).hostname for name in REALTIME_SERVICES
    }


def _compose_services(compose_file: str) -> dict[str, dict]:
    compose = yaml.safe_load((REPO_ROOT / compose_file).read_text(encoding="utf-8"))
    return compose["services"]


def _realtime_service(compose_file: str) -> dict:
    return _compose_services(compose_file)["realtime"]


def _environment(service: dict) -> dict[str, str]:
    env = service.get("environment") or {}
    if isinstance(env, list):
        env = dict(item.partition("=")[::2] for item in env)
    return {key: str(value) for key, value in env.items()}


def _tenant_name(service: dict) -> str:
    value = _environment(service).get("SELF_HOST_TENANT_NAME", SEED_DEFAULT_TENANT)
    match = COMPOSE_DEFAULT.match(value)
    return match.group("default") if match else value


def _supanet_aliases(service: dict) -> list[str]:
    networks = service.get("networks") or {}
    if isinstance(networks, list):
        return []
    return list((networks.get("supanet") or {}).get("aliases") or [])


def _acl_allow(kong_service: dict) -> list[str]:
    plugins = kong_service.get("plugins") or []
    acls = [p for p in plugins if p.get("name") == "acl"]
    assert acls, f"{kong_service['name']} has no acl plugin"
    return list(acls[0]["config"]["allow"])


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
def test_kong_upstream_host_is_a_supanet_alias_of_realtime(compose_file, kong_service):
    host = _kong_upstream_hosts()[kong_service]
    aliases = _supanet_aliases(_realtime_service(compose_file))
    assert host in aliases, (
        f"{kong_service} targets {host!r}, which is not a supanet alias of the "
        f"realtime service in {compose_file} (aliases: {aliases})"
    )


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
def test_realtime_alias_is_on_no_other_service(compose_file):
    hosts = set(_kong_upstream_hosts().values())
    for name, service in _compose_services(compose_file).items():
        if name == "realtime":
            continue
        shared = hosts & set(_supanet_aliases(service))
        assert not shared, f"{name} in {compose_file} also claims alias {shared}"


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
def test_realtime_seeds_its_tenant(compose_file):
    seed = _environment(_realtime_service(compose_file)).get("SEED_SELF_HOST")
    assert seed is not None and seed.lower() == "true", (
        f"realtime in {compose_file} must set SEED_SELF_HOST: true, or no tenant exists"
    )


@pytest.mark.parametrize("kong_service", REALTIME_SERVICES)
def test_kong_rewrites_the_host_to_the_upstream(kong_service):
    routes = _kong_services()[kong_service].get("routes") or []
    preserved = [r["name"] for r in routes if r.get("preserve_host")]
    assert not preserved, (
        f"{kong_service} routes {preserved} preserve the client Host, so Realtime "
        "would look up the public hostname as its tenant"
    )


def test_realtime_rest_api_is_admin_only():
    allow = _acl_allow(_kong_services()[REALTIME_REST_SERVICE])
    assert allow == ["admin"], (
        f"{REALTIME_REST_SERVICE} allows {allow}; its tenant API trusts any JWT "
        "signed with JWT_SECRET, so the anon key could rewrite the tenant"
    )
