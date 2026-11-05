"""Config-shape tests for the bloommcp tunnel entrance.

Outside traffic for bloommcp arrives through a Cloudflare tunnel: a `cloudflared`
container in each stack connects out to Cloudflare and forwards to Caddy's
`:8880` site, which refuses requests without `X-Salk-User`, replaces
`X-Bloom-Tunnel` with this stack's secret, and proxies to `bloommcp:8811`.
Neither publishes a port on the host. The tunnel is bloommcp's only way in from
outside: the public site has no bloommcp route and strips both headers.
"""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path

import pytest
import yaml

from tests.unit._caddyfile_helpers import block_after, site_block, strip_comments, text

REPO_ROOT = Path(__file__).resolve().parents[2]
PROD_COMPOSE = REPO_ROOT / "docker-compose.prod.yml"
DEV_COMPOSE = REPO_ROOT / "docker-compose.dev.yml"
DEPLOY = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
PROD_DEFAULTS = REPO_ROOT / ".env.prod.defaults"
STAGING_DEFAULTS = REPO_ROOT / ".env.staging.defaults"
PR_CHECKS = REPO_ROOT / ".github" / "workflows" / "pr-checks.yml"
CI_COMPOSE = REPO_ROOT / "docker-compose.ci.yml"
MAKEFILE = REPO_ROOT / "Makefile"

TUNNEL_PORT = "8880"
TUNNEL_PROFILE = "tunnel"
TUNNEL_NETWORK = "tunnel"
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
    command = service["command"]
    assert command[0] == "tunnel" and command[-1] == "run", command
    assert "--no-autoupdate" in command
    assert service["environment"]["TUNNEL_TOKEN"] == "${CLOUDFLARE_TUNNEL_TOKEN}"


def test_cloudflared_metrics_listen_on_loopback_only():
    command = _cloudflared()["command"]
    assert "--metrics" in command, "cloudflared metrics address is not set"
    index = command.index("--metrics")
    # A `tunnel` flag, so it must come before `run`.
    assert index < command.index("run")
    assert command[index + 1].startswith("127.0.0.1:"), command[index + 1]


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
    assert service.get("stop_grace_period") == "30s"
    # The image's own non-root user, pinned so a new image can't switch to root.
    assert service.get("user") == "65532:65532"


@pytest.mark.parametrize("target, env_file", [("prod-logs", ".env.prod"), ("staging-logs", ".env.staging")])
def test_log_targets_read_the_env_file_so_cloudflared_is_included(target: str, env_file: str):
    recipe = re.search(rf"(?m)^{target}:\n\t(.+)$", MAKEFILE.read_text(encoding="utf-8"))
    assert recipe, f"no {target} target in the Makefile"
    assert f"--env-file {env_file}" in recipe.group(1)


def test_cloudflared_memory_is_capped_without_swap():
    service = _cloudflared()
    assert service.get("mem_limit") == "256m"
    assert service.get("memswap_limit") == "256m"


def test_cloudflared_is_only_on_the_tunnel_network():
    """A wrong route on the Cloudflare side reaches Caddy, not db, studio, kong or minio."""
    services = _services()
    assert services["cloudflared"]["networks"] == [TUNNEL_NETWORK]
    assert "caddy" in services["cloudflared"]["depends_on"]


def test_caddy_is_on_the_tunnel_network_too():
    caddy = _services()["caddy"]
    assert TUNNEL_NETWORK in caddy["networks"]
    assert "supanet" in caddy["networks"], "Caddy reaches bloommcp over supanet"
    assert caddy.get("network_mode") is None


def test_only_caddy_and_cloudflared_share_the_tunnel_network():
    on_tunnel = {
        name for name, service in _services().items() if TUNNEL_NETWORK in service.get("networks", [])
    }
    assert on_tunnel == {"caddy", "cloudflared"}


def test_the_tunnel_network_can_reach_the_internet():
    networks = yaml.safe_load(PROD_COMPOSE.read_text(encoding="utf-8"))["networks"]
    assert TUNNEL_NETWORK in networks
    assert not (networks[TUNNEL_NETWORK] or {}).get("internal"), "cloudflared needs outbound internet"


def _env_value(path: Path, key: str) -> str | None:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return None


def test_the_tunnel_network_has_a_fixed_subnet_caddy_knows():
    networks = yaml.safe_load(PROD_COMPOSE.read_text(encoding="utf-8"))["networks"]
    assert networks[TUNNEL_NETWORK]["ipam"]["config"] == [{"subnet": "${TUNNEL_SUBNET}"}]
    assert _services()["caddy"]["environment"]["TUNNEL_SUBNET"] == "${TUNNEL_SUBNET}"


def test_tunnel_subnets_are_distinct_and_clear_of_salk_and_existing_networks():
    ci = re.search(r'TUNNEL_SUBNET=([0-9./]+)', PR_CHECKS.read_text(encoding="utf-8"))
    assert ci, "pr-checks.yml does not set TUNNEL_SUBNET in .env.ci"
    subnets = {
        "prod": _env_value(PROD_DEFAULTS, "TUNNEL_SUBNET"),
        "staging": _env_value(STAGING_DEFAULTS, "TUNNEL_SUBNET"),
        "ci": ci.group(1),
    }
    nets = {name: ipaddress.ip_network(value) for name, value in subnets.items()}
    # Salk's network, and the bridge and supanet ranges already on bloom-dev.
    taken = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.17.0.0/16", "172.18.0.0/16", "172.19.0.0/16")]
    names = list(nets)
    for i, name in enumerate(names):
        assert not any(nets[name].overlaps(t) for t in taken), f"{name} tunnel subnet overlaps {taken}"
        for other in names[i + 1 :]:
            assert not nets[name].overlaps(nets[other]), f"{name} and {other} tunnel subnets overlap"


def test_every_other_caddy_site_drops_tunnel_traffic():
    caddyfile = strip_comments(text())
    globals_block = block_after(caddyfile, r"\A\s*")
    assert globals_block is not None and "order abort first" in _lines(globals_block), (
        "abort runs after handle by default, so it must be ordered first"
    )
    # A site line may hold placeholders ({$X}); only the global block is a bare `{`.
    sites = re.findall(r"(?m)^(?!\{\s*$)(\S.*?)\s*\{\s*$", caddyfile)
    assert not re.search(r"(?m),\s*$", caddyfile), "site addresses must stay on one line"
    others = [s for s in sites if s != f":{TUNNEL_PORT}"]
    assert set(others) >= {"{$CADDY_SITE_ADDRESSES}", ":443", ":80", ":2021"}
    for site in others:
        body = block_after(caddyfile, rf"(?m)^{re.escape(site)}\s")
        assert body is not None, site
        top = _top_level(body)
        assert "@from_tunnel remote_ip {$TUNNEL_SUBNET}" in top, f"{site} does not match the tunnel subnet"
        assert "abort @from_tunnel" in top, f"{site} does not drop tunnel traffic"
    assert "@from_tunnel" not in _tunnel_site()


def test_no_service_publishes_the_tunnel_port():
    for name, service in _services().items():
        for mapping in service.get("ports", []):
            assert TUNNEL_PORT not in str(mapping), f"{name} publishes {mapping}"


def test_caddy_gets_the_tunnel_secret():
    caddy = _services()["caddy"]
    assert caddy["environment"]["BLOOMMCP_TUNNEL_SECRET"] == "${BLOOMMCP_TUNNEL_SECRET}"


def test_bloommcp_gets_the_tunnel_secret_and_site_url():
    env = _services()["bloommcp"]["environment"]
    assert env["BLOOMMCP_TUNNEL_SECRET"] == "${BLOOMMCP_TUNNEL_SECRET}"
    assert env["SITE_URL"] == "${SITE_URL}"


def test_dev_bloommcp_leaves_the_tunnel_off_by_default():
    services = yaml.safe_load(DEV_COMPOSE.read_text(encoding="utf-8"))["services"]
    env = services["bloommcp"]["environment"]
    assert env["BLOOMMCP_TUNNEL_SECRET"] == "${BLOOMMCP_TUNNEL_SECRET:-}"
    assert env["SITE_URL"] == "${SITE_URL:-}"


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
    """CI has no tunnel token; cloudflared would crash-loop and fail the health check.

    CI's env file is generated inside pr-checks.yml, so the workflow is what is checked.
    """
    pr_checks = PR_CHECKS.read_text(encoding="utf-8")
    assert "COMPOSE_PROFILES" not in pr_checks
    assert "--profile" not in pr_checks
    ci_services = yaml.safe_load(CI_COMPOSE.read_text(encoding="utf-8")).get("services") or {}
    for name, service in ci_services.items():
        assert "profiles" not in (service or {}), f"docker-compose.ci.yml sets profiles on {name}"


# --- Caddy :8880 site ------------------------------------------------------


def _tunnel_site() -> str:
    site = block_after(strip_comments(text()), rf"(?m)^:{TUNNEL_PORT}\s")
    assert site is not None, f"no :{TUNNEL_PORT} site in caddy/Caddyfile"
    return site


def _lines(body: str) -> list[str]:
    return [line.strip() for line in body.splitlines() if line.strip()]


def _top_level(body: str) -> list[str]:
    """The site's own lines, without what is inside its nested blocks."""
    lines, depth = [], 0
    for line in _lines(body):
        if depth == 0:
            lines.append(line)
        depth += line.count("{") - line.count("}")
    return [line for line in lines if line != "}"]


def test_tunnel_site_has_only_the_refusal_and_one_catch_all():
    """A path-specific handle is ordered before both and would skip the X-Salk-User check."""
    assert _top_level(_tunnel_site()) == [
        "@no_salk_user not header X-Salk-User *",
        "handle @no_salk_user {",
        "handle {",
    ]


def test_tunnel_site_refuses_requests_without_salk_user():
    site = _tunnel_site()
    matcher = re.search(r"^\s*(@\w+)\s+not\s+header\s+X-Salk-User\s+\*\s*$", site, re.M)
    assert matcher, "no matcher for a missing X-Salk-User"
    refusal = block_after(site, rf"handle\s+{matcher.group(1)}\b")
    assert refusal is not None
    assert _lines(refusal) == ['respond "forbidden" 403']


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


# --- Public site: no bloommcp route, tunnel headers stripped ---------------


def _public_site() -> str:
    site = site_block(strip_comments(text()))
    assert site is not None, "missing `{$CADDY_SITE_ADDRESSES}` site block"
    return site


def _site_level_lines(block: str) -> list[str]:
    lines, depth = [], 0
    for line in block.splitlines():
        if depth == 0 and line.strip():
            lines.append(line.strip())
        depth += line.count("{") - line.count("}")
    return lines


def test_public_site_has_no_bloommcp_route():
    site = _public_site()
    assert "bloommcp" not in site
    assert "oauth-protected-resource" not in site


def test_only_the_tunnel_site_reaches_bloommcp():
    everywhere = strip_comments(text()).count("bloommcp:8811")
    assert everywhere == _tunnel_site().count("bloommcp:8811") == 1


@pytest.mark.parametrize("header", ["X-Salk-User", "X-Bloom-Tunnel"])
def test_public_site_strips_the_tunnel_headers(header: str):
    """Site level, so every hostname and route in the block drops them."""
    assert f"request_header -{header}" in _site_level_lines(_public_site())


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


DEPLOY_JOBS = [
    ("deploy-production", "production", "docker compose -f docker-compose.prod.yml --env-file .env.prod"),
    (
        "deploy-staging",
        "staging",
        "docker compose -p bloom_v2_staging -f docker-compose.prod.yml --env-file .env.staging",
    ),
]


@pytest.mark.parametrize("job_name, label, compose", DEPLOY_JOBS)
def test_deploy_warns_but_never_fails_on_a_dead_tunnel(job_name: str, label: str, compose: str):
    steps = yaml.safe_load(DEPLOY.read_text(encoding="utf-8"))["jobs"][job_name]["steps"]
    names = [s.get("name") for s in steps]
    check = f"Tunnel connection check ({label})"
    assert check in names, f"{job_name} has no '{check}' step"
    assert names.index("Caddy crash-loop check") < names.index(check)
    step = steps[names.index(check)]
    assert "if" not in step, "the check must run on every deploy"
    assert step.get("continue-on-error") is True
    run = step["run"]
    assert "exit 1" not in run and "::error::" not in run
    assert f"{compose} ps -a -q cloudflared" in run
    assert "::warning::" in run


@pytest.mark.parametrize("job_name, label, compose", DEPLOY_JOBS)
def test_deploy_asks_cloudflared_whether_it_is_connected_now(job_name: str, label: str, compose: str):
    """The container usually outlives the deploy, so its log history and restart count
    say nothing about now; cloudflared's own ready check does."""
    steps = yaml.safe_load(DEPLOY.read_text(encoding="utf-8"))["jobs"][job_name]["steps"]
    run = next(s for s in steps if s.get("name") == f"Tunnel connection check ({label})")["run"]
    metrics = _cloudflared()["command"][_cloudflared()["command"].index("--metrics") + 1]
    assert f"cloudflared tunnel --metrics {metrics} ready" in run
    assert "Registered tunnel connection" not in run
    assert "RestartCount" not in run
    assert "{{.State.StartedAt}}" in run and "logs --since" in run, "logs shown start at this container's start"
    assert "sleep 30" not in run, "poll up to a named limit instead of a fixed wait"


@pytest.mark.parametrize("job_name, label, compose", DEPLOY_JOBS)
def test_deploy_warns_when_the_profile_is_on_but_cloudflared_is_missing(job_name: str, label: str, compose: str):
    steps = yaml.safe_load(DEPLOY.read_text(encoding="utf-8"))["jobs"][job_name]["steps"]
    run = next(s for s in steps if s.get("name") == f"Tunnel connection check ({label})")["run"]
    env_file = compose.split("--env-file ")[1]
    assert f"COMPOSE_PROFILES=.*tunnel' {env_file}" in run
    assert "ps -a -q cloudflared 2>/dev/null" not in run, "a compose error must not read as 'no tunnel'"
