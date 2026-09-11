"""Shape guard for the migration-isolation job in pr-checks.yml.

The job runs on both bases: the script itself decides what counts as a migration change,
so a promotion passes and a hotfix to main is still checked. It fails the check, so a PR
that mixes a migration with other code goes red.
"""
from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
PR_CHECKS = REPO_ROOT / ".github" / "workflows" / "pr-checks.yml"
JOB = "lint-migration-isolation"
BASE_SHA = "${{ github.event.pull_request.base.sha }}"


def _job() -> dict:
    workflow = yaml.safe_load(PR_CHECKS.read_text(encoding="utf-8"))
    job = workflow["jobs"].get(JOB)
    assert job is not None, f"pr-checks.yml has no {JOB!r} job"
    return job


def _lint_step(job: dict) -> dict:
    steps = [s for s in job["steps"] if "lint_migration_isolation.py" in str(s.get("run", ""))]
    assert len(steps) == 1, f"expected one step running lint_migration_isolation.py, found {len(steps)}"
    return steps[0]


def test_job_runs_on_both_bases():
    assert "base.ref" not in str(_job().get("if", "")), (
        f"{JOB} must not be gated on the base branch; the script exempts promotions itself"
    )


def test_checkout_has_full_history():
    checkout = next(s for s in _job()["steps"] if str(s.get("uses", "")).startswith("actions/checkout"))
    assert checkout.get("with", {}).get("fetch-depth") == 0, "the lint needs origin/staging and the base SHA"


def test_lint_passes_the_base_sha():
    assert BASE_SHA in _lint_step(_job())["run"]


def test_lint_step_fails_the_check():
    step = _lint_step(_job())
    assert "continue-on-error" not in step, "a failure that shows green lets a mixed PR merge"


def test_existing_migration_lint_keeps_its_required_name():
    workflow = yaml.safe_load(PR_CHECKS.read_text(encoding="utf-8"))
    assert workflow["jobs"]["lint-migrations"]["name"] == "Lint new migration filenames + timestamps"
