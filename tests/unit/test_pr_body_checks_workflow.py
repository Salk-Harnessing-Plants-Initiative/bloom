"""Shape guard for .github/workflows/pr-body-checks.yml.

Editing a PR description re-runs only this small workflow, not pr-checks.yml's image builds.
It has no paths filter (a required check skipped by one would hang), reads the body only
through env, and fails the check when a migration PR's body does not document the change.
"""
from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
BODY_CHECKS = WORKFLOWS / "pr-body-checks.yml"
BODY_EXPR = "github.event.pull_request.body"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _pull_request(workflow: dict) -> dict:
    """The `on:` block — safe_load reads the bare key as the boolean True."""
    return (workflow.get("on") or workflow[True])["pull_request"]


def _steps(workflow: dict) -> list[dict]:
    return [step for job in workflow["jobs"].values() for step in job["steps"]]


def _lint_step(workflow: dict) -> dict:
    steps = [s for s in _steps(workflow) if "lint_migration_pr_body.py" in str(s.get("run", ""))]
    assert len(steps) == 1, f"expected one step running lint_migration_pr_body.py, found {len(steps)}"
    return steps[0]


def test_triggers_on_body_edits_for_both_bases():
    trigger = _pull_request(_load(BODY_CHECKS))
    assert set(trigger["types"]) == {"opened", "edited", "synchronize", "reopened"}
    assert set(trigger["branches"]) == {"staging", "main"}


def test_has_no_paths_filter():
    assert "paths" not in _pull_request(_load(BODY_CHECKS))


def test_permissions_are_read_only():
    assert _load(BODY_CHECKS)["permissions"] == {"contents": "read"}


def test_concurrency_cancels_superseded_runs_per_pr():
    concurrency = _load(BODY_CHECKS)["concurrency"]
    assert "github.event.pull_request.number" in concurrency["group"]
    assert concurrency["cancel-in-progress"] is True


def test_body_reaches_the_script_only_through_env():
    workflow = _load(BODY_CHECKS)
    step = _lint_step(workflow)
    assert BODY_EXPR in str(step["env"]["PR_BODY"])
    for s in _steps(workflow):
        assert BODY_EXPR not in str(s.get("run", "")), "never interpolate the PR body into a shell command"


def test_checkout_has_full_history_and_passes_the_base_sha():
    workflow = _load(BODY_CHECKS)
    checkout = next(s for s in _steps(workflow) if str(s.get("uses", "")).startswith("actions/checkout"))
    assert checkout.get("with", {}).get("fetch-depth") == 0
    assert "${{ github.event.pull_request.base.sha }}" in _lint_step(workflow)["run"]


def test_lint_step_fails_the_check():
    step = _lint_step(_load(BODY_CHECKS))
    assert "continue-on-error" not in step, "a failure that shows green lets an undocumented migration merge"


def test_pr_checks_does_not_rerun_on_edits():
    trigger = _pull_request(_load(WORKFLOWS / "pr-checks.yml"))
    assert "edited" not in (trigger.get("types") or [])
