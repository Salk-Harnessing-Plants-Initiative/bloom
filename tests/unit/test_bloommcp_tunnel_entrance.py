"""Config-shape tests for the bloommcp tunnel entrance.

Outside traffic for bloommcp arrives through a Cloudflare tunnel: a `cloudflared`
container in each stack connects out to Cloudflare and forwards to Caddy's
`:8880` site, which refuses requests without `X-Salk-User`, replaces
`X-Bloom-Tunnel` with this stack's secret, and proxies to `bloommcp:8811`.
Neither publishes a port on the host.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.unit._caddyfile_helpers import block_after, strip_comments, text

REPO_ROOT = Path(__file__).resolve().parents[2]
PROD_COMPOSE = REPO_ROOT / "docker-compose.prod.yml"
DEPLOY = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
PROD_DEFAULTS = REPO_ROOT / ".env.prod.defaults"
STAGING_DEFAULTS = REPO_ROOT / ".env.staging.defaults"
CI_ENV = REPO_ROOT / ".env.ci"
PR_CHECKS = REPO_ROOT / ".github" / "workflows" / "pr-checks.yml"

TUNNEL_PORT = "8880"
TUNNEL_PROFILE = "tunnel"
GHCR_PREFIX = "ghcr.io/salk-harnessing-plants-initiative/"
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")


def _services() -> dict:
    return yaml.safe_load(PROD_COMPOSE.read_text(encoding="utf-8"))["services"]


def _cloudflared() -> dict:
    services = _services()
    assert "cloudflared" in services, "no cloudflared service in docker-compose.prod.yml"
    return services["cloudflared"]


# --- cloudflared container -------------------------------------------------


def test_cloudflared_runs_a_pinned_ghcr_copy():
    image = _cloudflared()["image"]
    assert image.startswith(GHCR_PREFIX + "cloudflare-cloudflared:"), image
    assert DIGEST.search(image), f"cloudflared image is not digest-pinned: {image}"


def test_cloudflared_is_in_the_tunnel_profile():
    assert _cloudflared().get("profiles") == [TUNNEL_PROFILE]


def test_cloudflared_runs_the_tunnel_from_the_token():
    service = _cloudflared()
    assert service["command"] == ["tunnel", "--no-autoupdate", "run"]
    assert service["environment"]["TUNNEL_TOKEN"] == "${CLOUDFLARE_TUNNEL_TOKEN}"


def test_cloudflared_publishes_nothing():
    service = _cloudflared()
    assert "ports" not in service
    assert service.get("network_mode") is None


def test_cloudflared_is_hardened():
    service = _cloudflared()
    assert service.get("read_only") is True
    assert service.get("cap_drop") == ["ALL"]
    assert "cap_add" not in service
    assert "no-new-privileges:true" in service.get("security_opt", [])
    assert service.get("restart") == "unless-stopped"
    assert service.get("mem_limit")


def test_cloudflared_shares_caddys_network_and_waits_for_it():
    services = _services()
    assert services["cloudflared"]["networks"] == services["caddy"]["networks"]
    assert "caddy" in services["cloudflared"]["depends_on"]


def test_no_service_publishes_the_tunnel_port():
    for name, service in _services().items():
        for mapping in service.get("ports", []):
            assert TUNNEL_PORT not in str(mapping), f"{name} publishes {mapping}"


def test_caddy_gets_the_tunnel_secret():
    caddy = _services()["caddy"]
    assert caddy["environment"]["BLOOMMCP_TUNNEL_SECRET"] == "${BLOOMMCP_TUNNEL_SECRET}"


# --- Profile: on for prod and staging, off in CI ---------------------------


def _env_value(path: Path, key: str) -> str | None:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.partition("=")[2]
    return None


@pytest.mark.parametrize("path", [PROD_DEFAULTS, STAGING_DEFAULTS], ids=lambda p: p.name)
def test_prod_and_staging_enable_the_tunnel_profile(path: Path):
    value = _env_value(path, "COMPOSE_PROFILES")
    assert value is not None, f"{path.name} does not set COMPOSE_PROFILES"
    assert TUNNEL_PROFILE in value.split(",")


def test_ci_does_not_enable_the_tunnel_profile():
    """CI has no tunnel token; cloudflared would crash-loop and fail the health check."""
    assert _env_value(CI_ENV, "COMPOSE_PROFILES") is None
    pr_checks = PR_CHECKS.read_text(encoding="utf-8")
    assert "COMPOSE_PROFILES" not in pr_checks
    assert "--profile" not in pr_checks


# --- Caddy :8880 site ------------------------------------------------------


def _tunnel_site() -> str:
    site = block_after(strip_comments(text()), rf"(?m)^:{TUNNEL_PORT}\s")
    assert site is not None, f"no :{TUNNEL_PORT} site in caddy/Caddyfile"
    return site


def _lines(body: str) -> list[str]:
    return [line.strip() for line in body.splitlines() if line.strip()]


def test_tunnel_site_refuses_requests_without_salk_user():
    site = _tunnel_site()
    matcher = re.search(r"^\s*(@\w+)\s+not\s+header\s+X-Salk-User\s+\*\s*$", site, re.M)
    assert matcher, "no matcher for a missing X-Salk-User"
    refusal = block_after(site, rf"handle\s+{matcher.group(1)}\b")
    assert refusal is not None
    assert _lines(refusal) == ["respond 403"]


def test_tunnel_site_proxies_to_bloommcp():
    site = _tunnel_site()
    proxy = block_after(site, r"reverse_proxy\s+bloommcp:8811\b")
    assert proxy is not None, "tunnel site does not proxy to bloommcp:8811"
    assert "flush_interval -1" in _lines(proxy)


def test_tunnel_site_replaces_the_tunnel_header():
    site = _tunnel_site()
    assert "request_header -X-Bloom-Tunnel" in _lines(site)
    proxy = block_after(site, r"reverse_proxy\s+bloommcp:8811\b")
    assert proxy is not None
    assert "header_up X-Bloom-Tunnel {env.BLOOMMCP_TUNNEL_SECRET}" in _lines(proxy)


def test_tunnel_site_forwards_paths_unchanged_and_uncompressed():
    site = _tunnel_site()
    assert "handle_path" not in site
    assert not re.search(r"^\s*(uri|rewrite)\b", site, re.M)
    assert not re.search(r"^\s*encode\b", site, re.M)


# --- Deploy workflow -------------------------------------------------------


def _secrets_heredoc(env: str) -> list[str]:
    workflow = yaml.safe_load(DEPLOY.read_text(encoding="utf-8"))
    job = workflow["jobs"][f"deploy-{'production' if env == 'prod' else 'staging'}"]
    step = next(
        s for s in job["steps"] if s.get("name") == f"Append secrets, validate, and promote to .env.{env}"
    )
    body = step["run"].split("<< 'SECRETS'", 1)[1].split("\nSECRETS", 1)[0]
    return _lines(body)


@pytest.mark.parametrize("env, prefix", [("prod", "PROD"), ("staging", "STAGING")])
@pytest.mark.parametrize("key", ["CLOUDFLARE_TUNNEL_TOKEN", "BLOOMMCP_TUNNEL_SECRET"])
def test_deploy_writes_the_tunnel_secrets(env: str, prefix: str, key: str):
    assert f"{key}=${{{{ secrets.{prefix}_{key} }}}}" in _secrets_heredoc(env)


# --- Env validation --------------------------------------------------------


@pytest.mark.parametrize("missing", ["CLOUDFLARE_TUNNEL_TOKEN", "BLOOMMCP_TUNNEL_SECRET"])
def test_deploy_validation_refuses_a_missing_tunnel_secret(tmp_path: Path, missing: str):
    """validate_env.sh derives its required keys from compose, so both are required."""
    referenced = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", PROD_COMPOSE.read_text()))
    required = referenced - {"COMPOSE_PROJECT_NAME", "NEXT_PUBLIC_SUPABASE_COOKIE_NAME", missing}
    defaults_text = PROD_DEFAULTS.read_text(encoding="utf-8")
    defaults_keys = {
        line.split("=", 1)[0]
        for line in defaults_text.splitlines()
        if line and not line.startswith("#") and "=" in line
    }
    appended = "\n".join(f"{k}=fake-{k.lower()}" for k in sorted(required - defaults_keys))
    env_file = tmp_path / ".env.test"
    env_file.write_text(defaults_text.rstrip("\n") + "\n" + appended + "\n# _EOF_MARKER_\n")
    result = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "validate_env.sh"), str(env_file), str(PROD_COMPOSE)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert f"- {missing}" in result.stderr
