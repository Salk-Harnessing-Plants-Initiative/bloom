"""CI must run the documented dev workflow end-to-end on the dev compose.

The existing `compose-health-check` job uses `docker-compose.prod.yml`, so the
dev path (`make init` -> `make dev-up` -> `make migrate-local` -> `make check`)
is never run live. This test pins a `pr-checks.yml` job that does, so a
regression in the dev workflow fails CI instead of a developer.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PR_CHECKS = REPO_ROOT / ".github" / "workflows" / "pr-checks.yml"

REQUIRED_COMMANDS = ("make init", "make dev-up", "make migrate-local", "make check")

# Maps each guarded step to its own required `if:` condition (see #455).
GUARDED_ENV_DEV_STEPS = {
    "Cleanup": "always()",
    "Debug logs on failure": "failure()",
}


def _first_run_line(step: dict) -> str:
    run = str(step.get("run") or "")
    lines = run.strip().splitlines()
    assert lines, "step has an empty run: block"
    return lines[0].strip()


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


def _dev_stack_smoke_job() -> dict:
    workflow = yaml.safe_load(PR_CHECKS.read_text(encoding="utf-8"))
    jobs = workflow.get("jobs", {})
    smoke = [
        job
        for job in jobs.values()
        if all(c in _job_run_text(job) for c in REQUIRED_COMMANDS)
    ]
    assert smoke, "dev-stack smoke job not found"
    assert len(smoke) == 1, "expected exactly one dev-stack-smoke job"
    return smoke[0]


def test_dev_stack_smoke_teardown_steps_guard_missing_env_dev():
    """Regression guard for #455: Cleanup (`if: always()`) and Debug logs on
    failure (`if: failure()`) both depend on .env.dev (written by `make
    init`). pr-checks.yml's concurrency block is workflow-level, so a
    superseding push can cancel this job before `make init` ever runs, and
    an earlier unrelated step can fail before it too — either step must
    no-op instead of crashing with `couldn't find env file`."""
    job = _dev_stack_smoke_job()
    by_name = {step.get("name"): step for step in job.get("steps") or []}
    for step_name, expected_if in GUARDED_ENV_DEV_STEPS.items():
        step = by_name.get(step_name)
        assert step is not None, f"dev-stack-smoke has no step named {step_name!r}"
        assert (
            step.get("if") == expected_if
        ), f"{step_name} must keep if: {expected_if}, got {step.get('if')!r}"
        first_line = _first_run_line(step)
        assert first_line.startswith("[ -s .env.dev ]"), (
            f"dev-stack-smoke's {step_name!r} run: block must guard .env.dev "
            f"existence as its first line; got {first_line!r}"
        )


@pytest.mark.parametrize("file_state", ["absent", "empty", "nonempty"])
def test_dev_stack_smoke_guard_short_circuits_behaviorally(
    tmp_path: Path, file_state: str
) -> None:
    """Behavioral counterpart to the shape check above: actually execute each
    guard line rather than asserting its literal text (see the equivalent
    compose-health-check test for rationale)."""
    job = _dev_stack_smoke_job()
    by_name = {step.get("name"): step for step in job.get("steps") or []}
    env_file = tmp_path / ".env.dev"
    if file_state == "nonempty":
        env_file.write_text("SOME_VAR=x\n")
    elif file_state == "empty":
        env_file.write_text("")
    for step_name in GUARDED_ENV_DEV_STEPS:
        guard = _first_run_line(by_name[step_name])
        result = subprocess.run(
            ["bash", "-c", f"{guard}\necho REACHED_PAST_GUARD"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 0, (
            f"{step_name}'s guard exited {result.returncode} for "
            f"file_state={file_state!r}; expected 0 regardless. "
            f"stderr: {result.stderr}"
        )
        if file_state == "nonempty":
            assert "REACHED_PAST_GUARD" in result.stdout, (
                f"{step_name}'s guard did not let execution continue past "
                f"it when .env.dev is non-empty; stdout: {result.stdout!r}"
            )
        else:
            assert "REACHED_PAST_GUARD" not in result.stdout, (
                f"{step_name}'s guard let execution continue past it when "
                f".env.dev is {file_state}; it must skip instead. "
                f"stdout: {result.stdout!r}"
            )
            assert "skipping" in result.stdout.lower(), (
                f"{step_name}'s guard printed no skip message; "
                f"stdout: {result.stdout!r}"
            )


def test_dev_stack_smoke_runs_the_plot_tool_check_after_bloommcp_smoke():
    """Issue #472: bloommcp-smoke (in-process, env-overridden to host temp dirs)
    never touches the real bind-mounted PLOTS_DIR, so it cannot catch a
    regression there. The job must also call `make bloommcp-plot-smoke` — a real
    MCP-transport call into the running container — after bloommcp-smoke (so the
    stack, incl. migrations, is fully up first), so a regression in the
    bind-mounted write path fails CI instead of a developer."""
    job = _dev_stack_smoke_job()
    run_lines = [
        line
        for step in job.get("steps") or []
        if isinstance(step.get("run"), str)
        for line in step["run"].splitlines()
    ]
    smoke_idx = next(
        (i for i, line in enumerate(run_lines) if "make bloommcp-smoke" in line), None
    )
    plot_idx = next(
        (i for i, line in enumerate(run_lines) if "make bloommcp-plot-smoke" in line),
        None,
    )
    assert smoke_idx is not None, "make bloommcp-smoke step not found"
    assert plot_idx is not None, "make bloommcp-plot-smoke step not found"
    assert plot_idx > smoke_idx, (
        "make bloommcp-plot-smoke must run AFTER make bloommcp-smoke, once the "
        "dev stack is fully migrated and up"
    )
