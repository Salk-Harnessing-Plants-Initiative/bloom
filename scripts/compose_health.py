#!/usr/bin/env python3
"""Compose service health: is every declared service up and healthy?

Shared by `make check` (dev stack, lenient) and the compose-health-check CI job
(prod stack, strict) so one implementation decides what "healthy" means.

A service is broken when its healthcheck says so, when it exited non-zero, when
it sits in a state that reports neither (restarting, created, dead, paused), or
— under require_all — when it has no container at all. `docker compose ps`
omits non-running containers unless given `-a`, so a crashed service is absent
rather than failing, which is why the completeness check exists (issue #163).
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = REPO_ROOT / "docker-compose.dev.yml"
ENV_DEV = REPO_ROOT / ".env.dev"


# Services that require user-supplied config to become healthy and are NOT part
# of the core dev substrate, so an unhealthy one is a warning, not a failure:
#   - langchain-agent needs LOCAL_LLM_URL / OPENAI_API_KEY (it builds a model at
#     import; with no LLM configured it can't start).
OPTIONAL_SERVICES = {"langchain-agent"}


def _classify_service_rows(
    rows: list[dict], strict: bool = False
) -> tuple[list[str], list[str]]:
    """Split service-health issues into hard problems vs optional warnings.

    Pure (no docker) so it is unit-testable. A service in OPTIONAL_SERVICES that
    is unhealthy/exited yields a warning; any other does a problem. ``strict``
    drops that leniency, for callers that gate a merge rather than a dev box.
    """
    problems: list[str] = []
    warnings: list[str] = []
    for svc in rows:
        name = svc.get("Service") or svc.get("Name", "?")
        health = (svc.get("Health") or "").lower()
        state = (svc.get("State") or "").lower()
        issue = None
        if health and health not in ("healthy", ""):
            issue = f"service {name} health={health}"
        elif state == "exited" and str(svc.get("ExitCode", "0")) not in ("0", "None"):
            issue = f"service {name} exited (code {svc.get('ExitCode')})"
        elif state and state not in ("running", "exited"):
            # restarting/created/dead/paused report no healthcheck result and no
            # exit code, so neither test above sees them.
            issue = f"service {name} state={state}"
        if not issue:
            continue
        if name in OPTIONAL_SERVICES and not strict:
            warnings.append(
                f"{issue} (optional — set LOCAL_LLM_URL / OPENAI_API_KEY to enable it)"
            )
        else:
            problems.append(issue)
    return problems, warnings


def _services_still_settling(rows: list[dict], strict: bool = False) -> list[str]:
    """Required (non-optional) services that may still reach a good state.

    Healthchecks first fire ~30s after start (e.g. bloommcp/realtime), so a
    `make check` run right after `make dev-up` can catch them mid-`starting`.
    Treat that as 'keep waiting', not a failure. A container in `created` or
    `restarting` may also still settle; if it is still there at the deadline,
    _classify_service_rows turns it into a problem.
    """
    out = []
    for svc in rows:
        name = svc.get("Service") or svc.get("Name", "?")
        if name in OPTIONAL_SERVICES and not strict:
            continue
        health = (svc.get("Health") or "").lower()
        state = (svc.get("State") or "").lower()
        if health == "starting" or state in ("created", "restarting"):
            out.append(name)
    return out


def _compose_args(compose_files: list[Path] | None, env_file: Path | None) -> list[str]:
    """The `docker compose -f ... --env-file ...` prefix shared by the queries."""
    files = compose_files or [COMPOSE_FILE]
    args = ["docker", "compose"]
    for f in files:
        args += ["-f", str(f)]
    args += ["--env-file", str(env_file or ENV_DEV)]
    return args


def _parse_ps_json(raw: str) -> tuple[list[dict] | None, list[str]]:
    """Parse `ps --format json` output. Pure, so both shapes are unit-testable."""
    # Newer compose prints one JSON object per line; older prints a JSON array.
    try:
        if raw.startswith("["):
            rows = json.loads(raw)
        else:
            rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    except json.JSONDecodeError as exc:
        return None, [f"could not parse `docker compose ps` output: {exc}"]
    # An unrecognised shape must fail closed, not read as "nothing is broken".
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        return None, [f"unexpected `docker compose ps` shape: {raw[:120]}"]
    for row in rows:
        if "Name" not in row and "Service" not in row:
            return None, [f"`docker compose ps` row names no service: {row}"]
    return rows, []


def _compose_ps_rows(
    compose_files: list[Path] | None = None,
    env_file: Path | None = None,
    all_containers: bool = False,
    services: list[str] | None = None,
) -> tuple[list[dict] | None, list[str]]:
    """Query `docker compose ps`. Return (rows, problems); rows is None on error.

    ``all_containers`` adds `-a`: without it compose omits every non-running
    container, so a service that crashed and stayed down is simply absent.
    """
    cmd = _compose_args(compose_files, env_file) + ["ps"]
    if all_containers:
        cmd.append("-a")
    cmd += ["--format", "json"]
    cmd += services or []
    try:
        out = subprocess.run(
            cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=30
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return None, [f"could not query docker compose: {exc}"]
    if out.returncode != 0:
        return None, [f"`docker compose ps` failed: {out.stderr.strip()[:200]}"]
    raw = out.stdout.strip()
    if not raw:
        return None, ["no compose services are running (did you `make dev-up`?)"]
    return _parse_ps_json(raw)


def _missing_services(expected: list[str], rows: list[dict]) -> list[str]:
    """Expected services with no container at all — invisible to every filter."""
    seen = {r.get("Service") or r.get("Name", "") for r in rows}
    return sorted(s for s in expected if s not in seen)


def _compose_config_services(
    compose_files: list[Path] | None = None, env_file: Path | None = None
) -> tuple[list[str] | None, list[str]]:
    """The services the compose files declare, for the completeness check."""
    cmd = _compose_args(compose_files, env_file) + ["config", "--services"]
    try:
        out = subprocess.run(
            cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=30
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return None, [f"could not list compose services: {exc}"]
    if out.returncode != 0:
        return None, [f"`docker compose config --services` failed: {out.stderr.strip()[:200]}"]
    return sorted(s.strip() for s in out.stdout.splitlines() if s.strip()), []


def check_services_healthy(
    timeout: float = 90.0,
    interval: float = 3.0,
    compose_files: list[Path] | None = None,
    env_file: Path | None = None,
    strict: bool = False,
    require_all: bool = False,
    services: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Return (problems, warnings) for compose service health, after bounded-
    waiting for required services to leave 'starting'. Optional LLM services that
    are down are warnings, not problems.

    ``strict`` drops the optional-service leniency. ``require_all`` additionally
    asserts every declared service has a container and is running, so a service
    that is absent cannot read as healthy. ``services`` narrows the check to
    the named ones, for the waits that gate on a single service coming up.
    """
    deadline = time.monotonic() + timeout
    expected: list[str] = []
    if require_all:
        expected, errors = _compose_config_services(compose_files, env_file)
        if expected is None:
            return errors, []
        if services:
            expected = [s for s in expected if s in services]
    while True:
        rows, errors = _compose_ps_rows(
            compose_files, env_file, all_containers=require_all, services=services
        )
        if rows is None:
            # A transient read fails the whole check only once the clock is up.
            if time.monotonic() >= deadline:
                return errors, []
            time.sleep(interval)
            continue
        missing = _missing_services(expected, rows) if require_all else []
        settling = _services_still_settling(rows, strict=strict)
        if (not settling and not missing) or time.monotonic() >= deadline:
            problems, warnings = _classify_service_rows(rows, strict=strict)
            problems += [f"service {name} has no container" for name in missing]
            return problems, warnings
        time.sleep(interval)


