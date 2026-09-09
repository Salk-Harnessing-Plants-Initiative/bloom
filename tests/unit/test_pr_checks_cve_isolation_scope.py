"""Regression guard: the CVE-isolation lint runs on staging-bound PRs only.

``scripts/lint_cve_isolation.sh`` fails a PR that touches ``.trivyignore``
alongside anything outside the CVE-fix surface (Dockerfiles and lockfiles). That
keeps a suppression reviewable in the PR where it is authored — and every such
PR targets ``staging`` under the feature -> staging -> main flow.

A main-bound PR is a promotion. It re-carries the ``.trivyignore`` edits staging
accumulated along with every other staged change, so the lint can only fail it,
however clean the diff is. Gating the job on the base branch keeps the signal
where it means something instead of turning it into a standing red check on
every promotion.
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
PR_CHECKS = REPO_ROOT / ".github" / "workflows" / "pr-checks.yml"

CVE_ISOLATION_JOB = "lint-cve-isolation"
EXPECTED_IF = "github.event.pull_request.base.ref == 'staging'"


def _load_workflow() -> dict:
    return yaml.safe_load(PR_CHECKS.read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    """The `on:` block — safe_load reads the bare key as the boolean True."""
    return workflow.get("on") or workflow[True]


def test_cve_isolation_job_is_gated_to_staging_base() -> None:
    workflow = _load_workflow()
    job = workflow["jobs"].get(CVE_ISOLATION_JOB)
    assert job is not None, f"pr-checks.yml has no {CVE_ISOLATION_JOB!r} job"
    assert job.get("if") == EXPECTED_IF, (
        f"{CVE_ISOLATION_JOB} must keep `if: {EXPECTED_IF}`, got {job.get('if')!r}. "
        "Without it the lint runs on main-bound promotion PRs, which always "
        "carry .trivyignore alongside unrelated staged changes and so always fail."
    )


def test_pr_checks_still_runs_on_both_bases() -> None:
    """The gate belongs on the job, not the workflow trigger — the rest of
    pr-checks.yml (CVE scans, audits, env parity) must still run on promotions.
    """
    workflow = _load_workflow()
    branches = _triggers(workflow)["pull_request"]["branches"]
    assert set(branches) == {"main", "staging"}, (
        "pr-checks.yml must keep running on both main- and staging-bound PRs; "
        f"got {branches!r}"
    )
