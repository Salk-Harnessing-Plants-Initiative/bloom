"""bloommcp's own Bloom login (Supabase Auth as an OAuth server, its consent
page and routes) is gone; users reach bloommcp through the Salk connector."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILES = [".env.prod.defaults", ".env.staging.defaults", ".env.dev.example"]
COMPOSE_FILES = ["docker-compose.prod.yml", "docker-compose.dev.yml"]
REMOVED_ENV = ("GOTRUE_OAUTH_SERVER_", "BLOOMMCP_OAUTH_")


@pytest.mark.parametrize("name", ENV_FILES)
def test_env_files_set_no_oauth_server_or_bloommcp_login(name):
    for line in (REPO_ROOT / name).read_text(encoding="utf-8").splitlines():
        key = line.split("=", 1)[0].strip()
        assert not key.startswith(REMOVED_ENV), f"{name} still sets {key}"
        if name != ".env.dev.example":
            assert key != "BLOOMMCP_PUBLIC_URL", f"{name} still sets BLOOMMCP_PUBLIC_URL"


@pytest.mark.parametrize("name", COMPOSE_FILES)
def test_compose_passes_no_oauth_server_or_bloommcp_login(name):
    services = yaml.safe_load((REPO_ROOT / name).read_text(encoding="utf-8"))["services"]
    for service_name, service in services.items():
        env = (service or {}).get("environment") or {}
        keys = env if isinstance(env, dict) else [e.split("=", 1)[0] for e in env]
        for key in keys:
            assert not key.startswith(REMOVED_ENV), f"{name}: {service_name} still gets {key}"


def test_kong_has_no_oauth_route():
    kong = yaml.safe_load((REPO_ROOT / "volumes/api/kong.yml").read_text(encoding="utf-8"))
    paths = [
        path
        for service in kong["services"]
        for route in service.get("routes") or []
        for path in route.get("paths") or []
    ]
    assert not [p for p in paths if p.startswith("/auth/v1/oauth")], paths
    assert "/auth/v1/.well-known" in paths, "the discovery and JWKS route stays"


def test_caddy_has_no_consent_route():
    assert "/api/oauth/consent" not in (REPO_ROOT / "caddy/Caddyfile").read_text(encoding="utf-8")


@pytest.mark.parametrize("path", ["web/app/oauth", "web/app/api/oauth/consent", "web/lib/oauth-consent.ts"])
def test_the_consent_page_is_gone(path):
    assert not (REPO_ROOT / path).exists(), f"{path} still exists"


def test_the_gitlab_oauth_route_stays():
    assert (REPO_ROOT / "web/app/api/oauth/gitlab").is_dir()
