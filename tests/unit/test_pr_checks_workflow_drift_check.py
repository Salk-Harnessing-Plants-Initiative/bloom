"""Regression guard for the vendored-Argo-Workflow drift-check job added to
pr-checks.yml (bloom #737).

Two failure modes this locks in specifically:

1. A job whose entire purpose is one external network fetch, with no
   timeout-minutes and no exit-code-masking, is exactly the shape that bit
   this repo before (issue #454 — a network call with no timeout wedged a job
   to GitHub's 6-hour default cap).
2. pr-checks.yml is a SINGLE workflow file shared by every other job
   (build-and-audit, docker-build, compose-health-check, ...). Scoping the new
   job to only run on relevant path changes must be done via a job-level
   `if:`, never by adding a `paths:` filter to the file's shared top-level
   `on.pull_request` trigger — that would silently scope every other job too,
   not just this one. This test asserts the top-level trigger is untouched.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
PR_CHECKS = REPO_ROOT / ".github" / "workflows" / "pr-checks.yml"

_RELEVANT_PATHS = (
    "services/workflows/vendored",
    "services/workflows/k8s_client.py",
    "services/workflows/pyproject.toml",
)


def _workflow() -> dict:
    return yaml.safe_load(PR_CHECKS.read_text(encoding="utf-8"))


def _on(workflow: dict) -> dict:
    """PyYAML parses the bare key ``on`` as the boolean ``True`` (YAML 1.1)."""
    return workflow.get("on") or workflow.get(True)


def _job_invoking_drift_script() -> tuple[str, dict]:
    wf = _workflow()
    for job_id, job in wf["jobs"].items():
        for step in job.get("steps") or []:
            run = str(step.get("run", ""))
            if "check_vendored_workflow_drift.py" in run:
                return job_id, job
    raise AssertionError(
        "no job in pr-checks.yml invokes scripts/check_vendored_workflow_drift.py"
    )


def test_top_level_pull_request_trigger_has_no_paths_filter() -> None:
    """The shared top-level trigger must stay exactly as it was — no `paths:`
    key added to scope it down for this one job's sake."""
    wf = _workflow()
    pull_request_trigger = _on(wf)["pull_request"]
    assert "paths" not in pull_request_trigger
    assert pull_request_trigger.get("branches") == ["main", "staging"]


def test_drift_check_job_exists_and_invokes_the_script() -> None:
    _job_invoking_drift_script()  # raises AssertionError if absent


def test_drift_check_job_has_an_explicit_timeout() -> None:
    _job_id, job = _job_invoking_drift_script()
    assert "timeout-minutes" in job
    assert isinstance(job["timeout-minutes"], int)
    assert job["timeout-minutes"] > 0


def test_drift_check_step_has_no_exit_code_masking() -> None:
    """The only realistic masking risk for a bare `run:` script invocation
    (unlike the Trivy `exit-code` action input, which doesn't apply here) is
    `continue-on-error: true` or shell-level `|| true` / `; exit 0`."""
    _job_id, job = _job_invoking_drift_script()
    for step in job.get("steps") or []:
        run = str(step.get("run", ""))
        if "check_vendored_workflow_drift.py" not in run:
            continue
        assert step.get("continue-on-error") is not True
        assert "|| true" not in run
        assert "exit 0" not in run


def test_drift_check_job_is_scoped_by_a_job_level_if_not_the_top_level_trigger() -> (
    None
):
    """Path-scoping must live on this job (directly, or via a `needs`-chain
    job it depends on), never on the shared top-level trigger asserted
    unchanged above."""
    wf = _workflow()
    job_id, job = _job_invoking_drift_script()

    if "if" in job:
        condition_source = str(job["if"])
        assert "needs." in condition_source or "github." in condition_source
        return

    needs = job.get("needs")
    assert needs, (
        f"job {job_id!r} has neither its own `if:` nor a `needs:` dependency — "
        "nothing scopes it to relevant path changes"
    )
    needed_jobs = [needs] if isinstance(needs, str) else list(needs)
    upstream = wf["jobs"][needed_jobs[0]]
    upstream_steps_text = "\n".join(
        str(step.get("run", "")) for step in upstream.get("steps") or []
    )
    for path in _RELEVANT_PATHS:
        assert path in upstream_steps_text, (
            f"expected the upstream job {needed_jobs[0]!r} to reference "
            f"{path!r} when computing whether to run the drift check"
        )
