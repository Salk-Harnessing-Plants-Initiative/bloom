"""PR checks must also run for the merge queue, or queued PRs wait forever for required checks.

A `merge_group` run has no `github.event.pull_request`: the base commit comes from
`github.event.merge_group`, PR comments have nowhere to go, and per-PR isolation lints
cannot judge a group that may hold several PRs.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PR_CHECKS = REPO_ROOT / ".github" / "workflows" / "pr-checks.yml"
PR_ONLY = "github.event_name == 'pull_request'"
STICKY_COMMENT = "marocchino/sticky-pull-request-comment"


def _workflow() -> dict:
    return yaml.safe_load(PR_CHECKS.read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    return workflow.get("on") or workflow[True]


def test_runs_for_the_merge_queue():
    merge_group = _triggers(_workflow())["merge_group"]
    assert merge_group == {"types": ["checks_requested"]}


def test_still_runs_for_pull_requests():
    assert _triggers(_workflow())["pull_request"]["branches"] == ["main", "staging"]


def test_base_commit_falls_back_to_the_merge_group():
    env = _workflow()["env"]
    assert env["BASE_SHA"] == (
        "${{ github.event.pull_request.base.sha || github.event.merge_group.base_sha }}"
    )
    assert env["BASE_REF"] == "${{ github.base_ref || github.event.merge_group.base_ref }}"


def test_no_step_reads_the_pull_request_base_directly():
    text = PR_CHECKS.read_text(encoding="utf-8")
    body = text.split("\njobs:\n", 1)[1]
    assert "github.event.pull_request.base.sha" not in body
    assert "github.base_ref" not in body


def test_migration_lint_strips_the_merge_group_ref_prefix():
    run = next(
        s["run"]
        for s in _workflow()["jobs"]["lint-migrations"]["steps"]
        if "lint_migrations.sh" in str(s.get("run", ""))
    )
    assert run.strip() == './scripts/lint_migrations.sh "origin/${BASE_REF#refs/heads/}"'


def test_pr_comments_are_skipped_in_the_queue():
    for name, job in _workflow()["jobs"].items():
        for step in job.get("steps", []):
            if str(step.get("uses", "")).startswith(STICKY_COMMENT):
                assert PR_ONLY in str(step.get("if", "")), (
                    f"{name}: {step.get('name')!r} posts a PR comment but runs in the queue"
                )


def test_migration_isolation_is_judged_per_pr():
    assert _workflow()["jobs"]["lint-migration-isolation"]["if"] == PR_ONLY


def test_cve_isolation_only_runs_on_staging_bound_prs():
    # pull_request is empty in the queue, so this condition is false there.
    assert (
        _workflow()["jobs"]["lint-cve-isolation"]["if"]
        == "github.event.pull_request.base.ref == 'staging'"
    )
