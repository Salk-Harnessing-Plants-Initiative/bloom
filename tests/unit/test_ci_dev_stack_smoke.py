"""CI must run the documented dev workflow end-to-end on the dev compose.

The existing `compose-health-check` job uses `docker-compose.prod.yml`, so the
dev path (`make init` -> `make dev-up` -> `make migrate-local` -> `make check`)
is never run live. This test pins a `pr-checks.yml` job that does, so a
regression in the dev workflow fails CI instead of a developer.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PR_CHECKS = REPO_ROOT / ".github" / "workflows" / "pr-checks.yml"

REQUIRED_COMMANDS = ("make init", "make dev-up", "make migrate-local", "make check")


def _job_run_text(job: dict) -> str:
    """Concatenate every step's `run` block in a job."""
    parts = []
    for step in job.get("steps", []) or []:
        run = step.get("run")
        if isinstance(run, str):
            parts.append(run)
    return "\n".join(parts)


def test_pr_checks_has_dev_stack_smoke_job():
    workflow = yaml.safe_load(PR_CHECKS.read_text(encoding="utf-8"))
    jobs = workflow.get("jobs", {})
    matching = [
        name
        for name, job in jobs.items()
        if all(cmd in _job_run_text(job) for cmd in REQUIRED_COMMANDS)
    ]
    assert matching, (
        "no pr-checks.yml job runs the full dev workflow "
        f"({', '.join(REQUIRED_COMMANDS)}); add a dev-stack smoke job so the dev "
        "path is exercised live on every PR."
    )


def test_dev_stack_smoke_skips_the_doctor_preflight():
    """`make dev-up` now runs `scripts/doctor.sh` first. On the known-good Linux
    runner that preflight is pure risk (its checks target local dev machines), so
    the dev-stack-smoke job must set DOCTOR_SKIP=1 on its dev-up invocation —
    otherwise a doctor false-positive turns a required check red."""
    workflow = yaml.safe_load(PR_CHECKS.read_text(encoding="utf-8"))
    jobs = workflow.get("jobs", {})
    smoke = [
        job
        for job in jobs.values()
        if all(c in _job_run_text(job) for c in REQUIRED_COMMANDS)
    ]
    assert smoke, "dev-stack smoke job not found"
    for job in smoke:
        dev_up_steps = [
            s
            for s in job.get("steps", []) or []
            if isinstance(s.get("run"), str) and "make dev-up" in s["run"]
        ]
        for step in dev_up_steps:
            has_env = str(step.get("env", {}).get("DOCTOR_SKIP", "")) == "1"
            has_inline = "DOCTOR_SKIP=1" in step["run"]
            assert has_env or has_inline, (
                "the dev-stack-smoke `make dev-up` step must set DOCTOR_SKIP=1 "
                "(step env or inline) so the doctor preflight is skipped in CI"
            )


def test_dev_stack_smoke_cleanup_guards_missing_env_dev():
    """Regression guard for #455: Cleanup runs `if: always()` and depends on
    .env.dev (written by `make init`). pr-checks.yml's concurrency block is
    workflow-level, so a superseding push can cancel this job before `make
    init` ever runs — Cleanup must no-op instead of crashing with `couldn't
    find env file`."""
    workflow = yaml.safe_load(PR_CHECKS.read_text(encoding="utf-8"))
    jobs = workflow.get("jobs", {})
    smoke = [
        job
        for job in jobs.values()
        if all(c in _job_run_text(job) for c in REQUIRED_COMMANDS)
    ]
    assert smoke, "dev-stack smoke job not found"
    for job in smoke:
        cleanup_steps = [
            s for s in job.get("steps", []) or [] if s.get("name") == "Cleanup"
        ]
        assert cleanup_steps, "dev-stack-smoke has no Cleanup step"
        for step in cleanup_steps:
            assert step.get("if") == "always()", "Cleanup must keep if: always()"
            run = str(step.get("run") or "")
            first_line = run.strip().splitlines()[0].strip() if run.strip() else ""
            assert first_line.startswith("[ -f .env.dev ]"), (
                "dev-stack-smoke's Cleanup run: block must guard .env.dev "
                f"existence as its first line; got {first_line!r}"
            )
