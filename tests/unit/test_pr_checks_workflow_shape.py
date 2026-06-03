"""Regression-guard test for pr-checks.yml shape under the GHCR migration.

Enforces requirements from
openspec/changes/add-ghcr-image-publishing/specs/image-publishing/spec.md
("PR CI Stack Builds Locally Via Overlay") and tasks.md §0.1.

Five invariants on .github/workflows/pr-checks.yml:

1. The ``docker-build`` job MUST NOT contain any ``--build-arg NEXT_PUBLIC_*``
   lines (after the runtime-config refactor, those args become silent no-ops
   on the Dockerfile that no longer declares them).
2. The ``compose-health-check`` job MUST declare a job-level
   ``env.COMPOSE_FILES`` value equal to
   ``-f docker-compose.prod.yml -f docker-compose.ci.yml`` so every compose
   command in the job applies both files via a single ``$COMPOSE_FILES``
   variable (prevents drift across the 6+ compose commands in that job).
3. Every ``docker compose`` invocation inside ``compose-health-check`` MUST
   use ``$COMPOSE_FILES`` (not hard-coded ``-f docker-compose.prod.yml``).
4. A new ``web-unit-tests`` job MUST exist that runs on ``ubuntu-latest``,
   executes ``cd web && npm run test:unit`` after ``npm ci``, and has no
   ``needs:`` block (runs in parallel with other jobs).
5. A Playwright step MUST exist in ``compose-health-check`` that runs
   ``cd web && npm run test:e2e`` after the stack is verified healthy.

Plus one assertion on the repo root:
6. ``docker-compose.ci.yml`` MUST exist and declare ``build:`` blocks for
   ``bloom-web``, ``langchain-agent``, and ``bloommcp`` so PR CI can keep
   building locally even after ``docker-compose.prod.yml`` flips its custom
   services to ``image: ghcr.io/...`` in PR-3.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
PR_CHECKS = REPO_ROOT / ".github" / "workflows" / "pr-checks.yml"
COMPOSE_CI = REPO_ROOT / "docker-compose.ci.yml"

EXPECTED_COMPOSE_FILES = "-f docker-compose.prod.yml -f docker-compose.ci.yml"
CUSTOM_SERVICES = ("bloom-web", "langchain-agent", "bloommcp")


def _load_workflow() -> dict:
    """Parse pr-checks.yml as YAML and return the dict."""
    return yaml.safe_load(PR_CHECKS.read_text(encoding="utf-8"))


def _iter_run_lines(job: dict) -> list[str]:
    """Flatten every ``run:`` string in a job's steps into individual lines.

    Joins backslash-continued physical lines into one logical line.
    """
    out: list[str] = []
    for step in job.get("steps") or []:
        run = step.get("run")
        if not run:
            continue
        # Join backslash continuations.
        joined = re.sub(r"\\\s*\n\s*", " ", str(run))
        for raw_line in joined.splitlines():
            stripped = raw_line.strip()
            if stripped:
                out.append(stripped)
    return out


def test_docker_build_job_has_no_next_public_build_args() -> None:
    """Invariant 1: no ``--build-arg NEXT_PUBLIC_*`` in docker-build."""
    workflow = _load_workflow()
    docker_build = workflow["jobs"]["docker-build"]
    offenders = [
        line
        for line in _iter_run_lines(docker_build)
        if "--build-arg" in line and "NEXT_PUBLIC_" in line
    ]
    assert not offenders, (
        "docker-build job still passes --build-arg NEXT_PUBLIC_*; "
        "after the runtime-config refactor these become silent no-ops "
        f"and must be removed.\nOffenders:\n  " + "\n  ".join(offenders)
    )


def test_compose_health_check_declares_compose_files_env() -> None:
    """Invariant 2: job-level env.COMPOSE_FILES is set correctly."""
    workflow = _load_workflow()
    job = workflow["jobs"]["compose-health-check"]
    env_block = job.get("env") or {}
    actual = env_block.get("COMPOSE_FILES")
    assert actual == EXPECTED_COMPOSE_FILES, (
        f"compose-health-check.env.COMPOSE_FILES must equal "
        f"{EXPECTED_COMPOSE_FILES!r}, got {actual!r}. The job-level env "
        "var prevents 6+ compose commands from drifting apart."
    )


def test_every_docker_compose_command_uses_compose_files_var() -> None:
    """Invariant 3: no compose command hard-codes -f docker-compose.prod.yml."""
    workflow = _load_workflow()
    job = workflow["jobs"]["compose-health-check"]
    offenders = []
    for line in _iter_run_lines(job):
        if "docker compose" not in line and "docker-compose" not in line:
            continue
        if "-f docker-compose.prod.yml" in line:
            offenders.append(line)
    assert not offenders, (
        "compose-health-check has docker compose commands that hard-code "
        "-f docker-compose.prod.yml; they must use $COMPOSE_FILES instead "
        f"so the ci.yml overlay applies uniformly.\nOffenders:\n  "
        + "\n  ".join(offenders)
    )


def test_web_unit_tests_job_exists_with_correct_shape() -> None:
    """Invariant 4: web-unit-tests job runs Vitest in parallel."""
    workflow = _load_workflow()
    jobs = workflow["jobs"]
    assert "web-unit-tests" in jobs, (
        "web-unit-tests job is missing from pr-checks.yml; it should run "
        "the new web/ Vitest suite added by this change."
    )
    job = jobs["web-unit-tests"]
    runs_on = job.get("runs-on")
    assert runs_on == "ubuntu-latest", (
        f"web-unit-tests.runs-on must be 'ubuntu-latest', got {runs_on!r}"
    )
    assert "needs" not in job, (
        "web-unit-tests must run in parallel (no needs:); found "
        f"needs={job.get('needs')!r}"
    )
    run_lines = _iter_run_lines(job)
    joined = " ".join(run_lines)
    assert "npm ci" in joined, (
        "web-unit-tests must run 'npm ci' before invoking the workspace"
    )
    assert any(
        "npm run test:unit" in line for line in run_lines
    ), "web-unit-tests must run 'cd web && npm run test:unit'"


def test_compose_health_check_runs_playwright_e2e() -> None:
    """Invariant 5: Playwright step exists in compose-health-check."""
    workflow = _load_workflow()
    job = workflow["jobs"]["compose-health-check"]
    run_lines = _iter_run_lines(job)
    has_playwright = any(
        "npm run test:e2e" in line for line in run_lines
    )
    assert has_playwright, (
        "compose-health-check must run 'cd web && npm run test:e2e' after "
        "the stack is healthy; this exercises the runtime-config refactor "
        "end-to-end (e.g. web/e2e/runtime-config.spec.ts in PR-3)."
    )


def test_docker_compose_ci_overlay_exists_with_build_blocks() -> None:
    """Invariant 6: docker-compose.ci.yml declares build: for custom services."""
    assert COMPOSE_CI.exists(), (
        "docker-compose.ci.yml is missing from the repo root; PR CI needs "
        "the overlay to restore build: blocks once docker-compose.prod.yml "
        "flips its custom services to image: ghcr.io/... in PR-3."
    )
    overlay = yaml.safe_load(COMPOSE_CI.read_text(encoding="utf-8"))
    services = overlay.get("services") or {}
    for service in CUSTOM_SERVICES:
        assert service in services, (
            f"docker-compose.ci.yml is missing service {service!r}; the "
            "overlay must declare build: for every custom service in "
            "docker-compose.prod.yml."
        )
        assert "build" in services[service], (
            f"docker-compose.ci.yml's {service!r} has no build: block; "
            "the overlay's whole purpose is to restore local-build "
            "context for PR CI."
        )


@pytest.mark.parametrize("service", CUSTOM_SERVICES)
def test_overlay_build_context_matches_prod(service: str) -> None:
    """Sanity: overlay's build context+dockerfile match prod compose."""
    prod = yaml.safe_load(
        (REPO_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    )
    overlay = yaml.safe_load(COMPOSE_CI.read_text(encoding="utf-8"))
    prod_build = (prod["services"][service].get("build") or {})
    overlay_build = overlay["services"][service]["build"]
    # Allow strings (shorthand context-only) or dicts.
    if isinstance(overlay_build, str):
        overlay_context = overlay_build
        overlay_dockerfile = None
    else:
        overlay_context = overlay_build.get("context")
        overlay_dockerfile = overlay_build.get("dockerfile")
    if isinstance(prod_build, str):
        prod_context = prod_build
        prod_dockerfile = None
    else:
        prod_context = prod_build.get("context")
        prod_dockerfile = prod_build.get("dockerfile")
    # Once PR-3 ships, prod_build will be empty (services flipped to image:).
    # Until then, contexts must match so the overlay is a true mirror.
    if prod_build:
        assert overlay_context == prod_context, (
            f"docker-compose.ci.yml's {service!r}.build.context "
            f"({overlay_context!r}) doesn't match prod compose's "
            f"({prod_context!r}); the overlay must mirror prod's build "
            "shape until PR-3 flips prod to image:."
        )
        assert overlay_dockerfile == prod_dockerfile, (
            f"docker-compose.ci.yml's {service!r}.build.dockerfile "
            f"({overlay_dockerfile!r}) doesn't match prod compose's "
            f"({prod_dockerfile!r})."
        )
