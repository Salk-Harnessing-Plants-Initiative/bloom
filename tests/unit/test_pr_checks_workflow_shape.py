"""Regression-guard test for pr-checks.yml shape under the GHCR migration.

Enforces requirements from
openspec/changes/add-ghcr-image-publishing/specs/image-publishing/spec.md
("PR CI Stack Builds Locally Via Overlay") and tasks.md §0.1.

Five invariants on .github/workflows/pr-checks.yml:

1. The ``compose-health-check`` job MUST declare a job-level
   ``env.COMPOSE_FILES`` value equal to
   ``-f docker-compose.prod.yml -f docker-compose.ci.yml`` so every compose
   command in the job applies both files via a single ``$COMPOSE_FILES``
   variable (prevents drift across the 6+ compose commands in that job).
2. Every ``docker compose`` invocation inside ``compose-health-check`` MUST
   use ``$COMPOSE_FILES`` (not hard-coded ``-f docker-compose.prod.yml``).
3. A new ``web-unit-tests`` job MUST exist that runs on ``ubuntu-latest``,
   executes ``cd web && npm run test:unit`` after ``npm ci``, and has no
   ``needs:`` block (runs in parallel with other jobs).
4. A Playwright step MUST exist in ``compose-health-check`` that runs
   ``cd web && npm run test:e2e`` after the stack is verified healthy.
5. The ``compose-health-check`` job MUST pin Node.js via
   ``actions/setup-node@v4`` with ``node-version: '20'`` BEFORE the
   Playwright/npm steps so CI is deterministic across runner-default
   changes (Copilot review finding on PR #268).

Plus one assertion on the repo root:
6. ``docker-compose.ci.yml`` MUST exist and declare ``build:`` blocks for
   ``bloom-web``, ``langchain-agent``, and ``bloommcp`` so PR CI can keep
   building locally even after ``docker-compose.prod.yml`` flips its custom
   services to ``image: ghcr.io/...`` in PR-3.

And a CI/prod build-arg parity assertion:
7. The ``Generate .env.ci`` step MUST set every env var referenced by a
   ``NEXT_PUBLIC_*`` build arg of the ``bloom-web`` service in
   ``docker-compose.prod.yml``. Any referenced var the generator omits gets
   substituted as empty when compose-health-check builds the image, baking an
   empty ``NEXT_PUBLIC_*`` into the JS bundle — silent CI/prod-shape drift.
   Copilot review on PR #268 flagged ``NEXT_PUBLIC_MCP_URL`` specifically.

The "no ``--build-arg NEXT_PUBLIC_*`` in docker-build" invariant has been
DEFERRED to PR-3 §6 (which removes the matching ``ARG`` lines from
``web/Dockerfile.bloom-web.prod`` so the build args become true no-ops).
Removing the flags here while the Dockerfile still bakes
``NEXT_PUBLIC_*`` breaks ``next build``'s ``/test/page`` prerender
because ``@supabase/ssr`` instantiates at module load and needs the
values present at build time.
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


# The "no --build-arg NEXT_PUBLIC_*" assertion is intentionally deferred
# to PR-3 §6. Until then web/Dockerfile.bloom-web.prod still declares
# `ARG NEXT_PUBLIC_*` and `next build` needs the values at build time
# (the `/test/page` prerender instantiates @supabase/ssr at module load).
# PR-3 §6 will simultaneously: (a) drop the ARG lines from the
# Dockerfile, (b) drop the --build-arg flags from pr-checks.yml, and
# (c) add tests/unit/test_dockerfile_no_next_public_args.py to fence
# both. See the module docstring above for the rationale.


def test_compose_health_check_declares_compose_files_env() -> None:
    """Invariant 1: job-level env.COMPOSE_FILES is set correctly."""
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
    """Invariant 2: no compose command hard-codes -f docker-compose.prod.yml."""
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
    """Invariant 3: web-unit-tests job runs Vitest in parallel."""
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
    """Invariant 4: Playwright step exists in compose-health-check."""
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


def test_compose_health_check_pins_node_20_before_npm_steps() -> None:
    """Invariant 5: actions/setup-node@v4 with Node 20 precedes npm/Playwright.

    Copilot review on PR #268 flagged that compose-health-check runs
    ``npm ci`` and Playwright without pinning a Node version, unlike the
    parallel web-unit-tests job that explicitly uses Node 20. Without a
    pin the runner's default Node is used, which can change between
    runner releases and produce non-deterministic CI behavior.
    """
    workflow = _load_workflow()
    job = workflow["jobs"]["compose-health-check"]
    steps = job.get("steps") or []
    setup_node_idx: int | None = None
    first_npm_idx: int | None = None
    for idx, step in enumerate(steps):
        uses = step.get("uses") or ""
        if uses.startswith("actions/setup-node@"):
            # Tolerate v4 (current) or any newer major; the spec only
            # requires the action to be present and pin Node 20.
            assert uses.startswith("actions/setup-node@v"), (
                f"compose-health-check uses {uses!r} for setup-node — "
                "must reference a pinned major version (e.g. v4)"
            )
            with_block = step.get("with") or {}
            node_version = with_block.get("node-version")
            assert node_version in (20, "20", "20.x"), (
                f"compose-health-check setup-node pins node-version={node_version!r}; "
                "must be '20' to match the web-unit-tests job and the "
                "runtime image (web/Dockerfile.bloom-web.prod uses node:20)."
            )
            setup_node_idx = idx
        run = step.get("run") or ""
        if first_npm_idx is None and (
            "npm ci" in str(run)
            or "npm run test:e2e" in str(run)
            or "npx playwright" in str(run)
        ):
            first_npm_idx = idx
    assert setup_node_idx is not None, (
        "compose-health-check must include an 'actions/setup-node@v4' step "
        "pinning Node 20 before any npm/Playwright step."
    )
    assert first_npm_idx is not None, (
        "compose-health-check appears to have no npm/Playwright step at all "
        "— inconsistent with Invariant 4 above"
    )
    assert setup_node_idx < first_npm_idx, (
        f"actions/setup-node@v4 is at step {setup_node_idx} but the first "
        f"npm/Playwright step is at {first_npm_idx}; the Node pin must "
        "come BEFORE any step that needs Node."
    )


def test_env_ci_sets_bloom_web_next_public_build_arg_vars() -> None:
    """Invariant 7: .env.ci generator sets every env var a bloom-web
    NEXT_PUBLIC_* build arg references.

    docker-compose.prod.yml bakes NEXT_PUBLIC_* into the bloom-web image via
    build args whose values are ``${ENV_VAR}`` references. compose-health-check
    builds that image with ``--env-file .env.ci``; any referenced var the
    generator omits substitutes to empty, silently shipping an empty
    NEXT_PUBLIC_* in the bundle. Copilot review on PR #268 caught
    NEXT_PUBLIC_MCP_URL missing from the generator.
    """
    prod = yaml.safe_load(
        (REPO_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    )
    build = prod["services"]["bloom-web"].get("build") or {}
    build_args = build.get("args") or {}
    # Collect the ${ENV_VAR} referenced by each NEXT_PUBLIC_* build arg value.
    referenced_vars: set[str] = set()
    for arg_name, arg_value in build_args.items():
        if not arg_name.startswith("NEXT_PUBLIC_"):
            continue
        for match in re.finditer(r"\$\{([A-Z_][A-Z0-9_]*)\}", str(arg_value)):
            referenced_vars.add(match.group(1))
    assert referenced_vars, (
        "expected bloom-web to declare NEXT_PUBLIC_* build args in "
        "docker-compose.prod.yml; found none — has the build shape changed?"
    )
    # Collect env vars the generator writes into .env.ci.
    workflow = _load_workflow()
    job = workflow["jobs"]["compose-health-check"]
    echoed: set[str] = set()
    for line in _iter_run_lines(job):
        if ">> .env.ci" not in line:
            continue
        match = re.search(r'echo\s+"([A-Z_][A-Z0-9_]*)=', line)
        if match:
            echoed.add(match.group(1))
    missing = sorted(referenced_vars - echoed)
    assert not missing, (
        "the 'Generate .env.ci from secrets' step does not set "
        f"{missing}, but docker-compose.prod.yml references them for "
        "bloom-web's NEXT_PUBLIC_* build args. The CI build would bake "
        "empty values — CI/prod-shape drift. Add an `echo \"VAR=...\" "
        ">> .env.ci` line for each."
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
