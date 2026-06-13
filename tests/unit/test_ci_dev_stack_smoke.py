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
