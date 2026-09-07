"""Regression guard for ``.github/workflows/weekly-backup.yml``.

The traps pinned here are the ones this repo has already been bitten by once, in
``refresh-cyl-experiment-trait-counts.yml``:

- A scheduled run routed through a GitHub Environment that carries a
  required-reviewer gate sits "Waiting" forever, because nobody approves a 02:00
  Sunday job. For a backup that is the worst possible failure: it looks
  configured, and it silently never runs. The scheduled path therefore resolves
  to a SEPARATE, ungated Environment while the target host stays ``production``
  — two deliberately different expressions that a well-meaning "simplification"
  would collapse into one.
- Sharing ``deploy-bloom`` as a concurrency group would let a queued or stuck
  deploy cancel the backup.
- The job must never delete on Box.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "weekly-backup.yml"

SCHEDULED_ENVIRONMENT = "production-scheduled-backup"


@pytest.fixture(scope="module")
def text() -> str:
    return WORKFLOW.read_text()


@pytest.fixture(scope="module")
def workflow() -> dict:
    loaded = yaml.safe_load(WORKFLOW.read_text())
    # PyYAML parses a bare `on:` key as the boolean True.
    loaded["on"] = loaded.pop(True, loaded.get("on"))
    return loaded


def test_workflow_exists(workflow):
    assert workflow["name"] == "Weekly Postgres backup"


# --------------------------------------------------------------------------
# Triggers
# --------------------------------------------------------------------------


def test_both_triggers_are_present(workflow):
    assert "schedule" in workflow["on"]
    assert "workflow_dispatch" in workflow["on"]


def test_cron_is_weekly_and_structurally_valid(workflow):
    cron = workflow["on"]["schedule"][0]["cron"]
    fields = cron.split()
    assert len(fields) == 5, f"cron must have five fields, got {cron!r}"
    minute, hour, dom, month, dow = fields
    assert dow == "0", "the backup is weekly, on Sunday"
    assert dom == "*" and month == "*"
    assert minute.isdigit() and int(minute) != 0, (
        "run off the top of the hour — GitHub's scheduler is most delayed there"
    )
    assert hour.isdigit()


def test_dispatch_offers_a_dry_run_and_an_environment_choice(workflow):
    # Staging is a real target: backup.py resolves the compose project per
    # environment (`-p bloom_v2_staging`), the way deploy.yml brings that stack
    # up. Without that `-p` a staging run would read staging's env file while
    # resolving the PRODUCTION container.
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert "dry_run" in inputs
    assert set(inputs["environment"]["options"]) == {"prod", "staging"}
    assert inputs["environment"]["default"] == "prod", (
        "the safe default for a hand-run backup is the environment the "
        "schedule uses, not the one someone last rehearsed with"
    )


def test_the_default_branch_requirement_is_documented(text):
    # Neither trigger fires until this file reaches `main`. Undocumented, the
    # first missing backup looks like a bug rather than a pending promotion.
    assert "DEFAULT branch" in text
    assert "workflow_dispatch" in text


# --------------------------------------------------------------------------
# The approval-gate trap
# --------------------------------------------------------------------------


def _job(workflow) -> dict:
    return workflow["jobs"]["backup"]


def test_scheduled_runs_use_an_ungated_environment(workflow):
    environment = _job(workflow)["environment"]
    assert SCHEDULED_ENVIRONMENT in environment, (
        "a scheduled run must not go through a gated Environment — it would "
        "wait for an approval that never comes, and the backup would silently "
        "never run"
    )
    assert "github.event_name == 'schedule'" in environment


def test_the_scheduled_environment_cannot_leak_into_the_dispatch_branch(workflow):
    # The dispatch half must be the raw input, so a manual production run still
    # goes through production's real approval gate.
    environment = _job(workflow)["environment"]
    _, _, dispatch_half = environment.partition("||")
    assert SCHEDULED_ENVIRONMENT not in dispatch_half
    assert "'production'" in dispatch_half


def test_the_target_host_and_the_approval_gate_stay_distinct(workflow, text):
    # The job's environment: (which gate to pass) resolves to the ungated name
    # on a schedule; the host it backs up is always the real production deploy
    # path. Collapsing the gate to `production` re-introduces the hang.
    job_environment = _job(workflow)["environment"]
    assert SCHEDULED_ENVIRONMENT in job_environment
    resolve = next(step for step in _job(workflow)["steps"]
                   if step.get("id") == "target")
    assert "secrets.PROD_DEPLOY_PATH" in resolve["env"]["PROD_DEPLOY_PATH"]
    assert SCHEDULED_ENVIRONMENT not in resolve["run"], (
        "the backup must target the real production host, not the gate's name"
    )


@pytest.mark.parametrize(
    "event_name,requested,expected_gate",
    [
        ("schedule", None, SCHEDULED_ENVIRONMENT),
        ("workflow_dispatch", "prod", "production"),
        ("workflow_dispatch", "staging", "staging"),
    ],
)
def test_the_approval_gate_truth_table(workflow, event_name, requested, expected_gate):
    """Evaluate the live GitHub expression, every branch, rather than restating it."""

    def evaluate(expression: str) -> str:
        # Nested `A && 'x' || (B && 'y' || 'z')`. Parse EVERY literal: a regex
        # reading only the first && half turns this into a tautology.
        match = re.fullmatch(
            r"\$\{\{\s*github\.event_name == 'schedule'\s*&&\s*'([^']*)'\s*"
            r"\|\|\s*\(\s*github\.event\.inputs\.environment == 'staging'\s*"
            r"&&\s*'([^']*)'\s*\|\|\s*'([^']*)'\s*\)\s*\}\}",
            expression.strip(),
        )
        assert match, f"unrecognised ternary: {expression}"
        scheduled, staging, production = match.groups()
        if event_name == "schedule":
            return scheduled
        return staging if requested == "staging" else production

    assert evaluate(_job(workflow)["environment"]) == expected_gate


def test_a_staging_rehearsal_is_not_gated_by_productions_reviewers(workflow):
    # And the reverse: production must not be reachable through staging's gate.
    environment = _job(workflow)["environment"]
    assert "'staging'" in environment
    assert "'production'" in environment
    assert SCHEDULED_ENVIRONMENT in environment


# --------------------------------------------------------------------------
# Concurrency, secrets, safety
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# The step that actually does the work
# --------------------------------------------------------------------------


def _step(workflow: dict, step_id: str) -> dict:
    return next(s for s in _job(workflow)["steps"] if s.get("id") == step_id)


def test_the_run_step_invokes_a_script_that_exists(workflow):
    # A typo here is a red run every Sunday and nothing else; no test covered
    # the command this job exists to issue.
    script = "scheduled-jobs/weekly-backup/backup.py"
    assert script in _step(workflow, "run")["run"]
    assert (REPO_ROOT / script).is_file(), "the workflow names a script that is not in the repo"


def test_the_run_step_fails_on_the_first_error_on_both_sides(workflow):
    # Without pipefail the ssh exit status is masked by tee, and the job goes
    # green on a failed backup.
    run = _step(workflow, "run")["run"]
    assert run.count("set -euo pipefail") == 2, "needed in the runner shell and the remote one"


def test_dry_run_can_only_come_from_the_dispatch_input(workflow):
    # Hardcoding --dry-run leaves every run green while Box receives nothing —
    # the worst failure this job has, because it looks like success.
    run = _step(workflow, "run")["run"]
    assert "--dry-run" not in run, "the flag belongs in the DRY_RUN expression, not the command"
    assert "${DRY_RUN}" in run
    dry_run = _step(workflow, "run")["env"]["DRY_RUN"]
    assert "github.event.inputs.dry_run" in dry_run
    assert "'--dry-run'" in dry_run


def test_paths_crossing_the_ssh_boundary_are_quoted(workflow):
    # The deploy path is a secret and could contain a space; unquoted, `cd`
    # lands somewhere else and the backup runs against whatever is there.
    # Every occurrence must be quoted — asserting that one of them is passes
    # while another sits bare.
    run = _step(workflow, "run")["run"]
    for name in ("DEPLOY_PATH", "ENV_NAME"):
        token = "${%s}" % name
        offsets = [m.start() for m in re.finditer(re.escape(token), run)]
        assert offsets, f"{token} is never used"
        for offset in offsets:
            before = run[offset - 1] if offset else ""
            after = run[offset + len(token):offset + len(token) + 1]
            assert before == "'" and after == "'", (
                f"{token} at offset {offset} crosses the ssh boundary unquoted"
            )


def test_the_summary_reads_the_file_the_run_step_wrote(workflow):
    # Two independently written paths that must agree; if they drift, every
    # summary is empty and nobody learns anything from the weekly check.
    written = re.findall(r'tee "([^"]+)"', _step(workflow, "run")["run"])
    read = re.findall(r'"(\$\{RUNNER_TEMP\}[^"]*)"', _step(workflow, "summary")["run"])
    assert written, "the run step must capture its output for the summary"
    assert read, "the summary step must read that captured output"
    assert set(written) == set(read), f"run wrote {written}, summary read {read}"


def test_the_single_runner_dependency_is_documented(text):
    # Nothing in the two workflows' concurrency groups keeps a backup off a
    # database mid-migration; what does is that both land on one self-hosted
    # runner. That is invisible in the YAML, so it has to be written down or it
    # stops protecting anything the day a second runner is registered.
    assert "SINGLE RUNNER" in text
    assert "deploy.yml" in text
    assert "second runner" in text


def test_each_environment_resolves_its_own_deploy_path(text):
    # The failure worth guarding: one environment's env name paired with the
    # other's deploy path, which is how a "staging" run dumps production.
    assert "ENV_NAME=prod" in text and "ENV_NAME=staging" in text
    prod_branch = text.split("prod)", 1)[1].split("staging)", 1)[0]
    staging_branch = text.split("staging)", 1)[1].split("*)", 1)[0]
    assert "PROD_DEPLOY_PATH" in prod_branch and "STAGING" not in prod_branch
    assert "STAGING_DEPLOY_PATH" in staging_branch


def test_an_unrecognised_environment_does_not_fall_through_to_production(text):
    # A new option added to the input's list with no branch here must fail the
    # run, not silently inherit whichever path the case statement leaves set.
    assert "unknown environment" in text


def test_a_scheduled_run_uses_the_default_environment(text):
    # A schedule carries no inputs at all, so the || default is what it gets.
    assert "github.event.inputs.environment || 'prod'" in text


def test_concurrency_is_not_the_shared_deploy_group(workflow):
    group = workflow["concurrency"]["group"]
    assert "deploy-bloom" not in group, (
        "a stuck deploy must not be able to cancel the backup"
    )
    assert workflow["concurrency"]["cancel-in-progress"] is False


def test_a_staging_rehearsal_cannot_queue_behind_production(workflow):
    # Drop the environment from the group and the two stacks share one slot.
    assert "inputs.environment" in workflow["concurrency"]["group"]


def test_an_approval_on_the_manual_path_does_not_hold_the_schedule(workflow):
    # A manual run sits in Waiting for its reviewer while holding its group.
    # Shared with the schedule, that approval also stalls the Sunday run.
    assert "github.event_name" in workflow["concurrency"]["group"]


def test_the_deploy_key_does_not_outlive_the_job(workflow, text):
    # The runner is shared with deploy.yml, which removes its own key twice.
    steps = _job(workflow)["steps"]
    cleanup = [s for s in steps if "rm -f ~/.ssh/deploy_key" in (s.get("run") or "")]
    assert cleanup, "the deploy key is written to the runner and never removed"
    assert all(s.get("if") == "always()" for s in cleanup), (
        "a key left behind by a failed run is the case that matters most"
    )
    assert "chmod 600 ~/.ssh/deploy_key" in text


def test_the_ssh_setup_step_is_present(workflow):
    # Deleting the whole step passed every test in this file.
    steps = _job(workflow)["steps"]
    assert any(
        "~/.ssh/deploy_key" in (s.get("run") or "") and "mkdir -p ~/.ssh" in (s.get("run") or "")
        for s in steps
    ), "no step establishes the SSH identity this job runs on"


def test_host_key_checking_is_never_disabled(text):
    # The pinned known_hosts is what stops this job trusting a new host.
    assert "StrictHostKeyChecking" not in text


def test_a_failed_backup_cannot_report_success(workflow):
    # continue-on-error turns a failed backup into a green run.
    for step in _job(workflow)["steps"]:
        assert step.get("continue-on-error") is not True, (
            f"step {step.get('name')!r} would hide a failed backup"
        )
    assert _job(workflow).get("continue-on-error") is not True


def test_the_run_is_bounded_by_a_timeout(workflow):
    # Cancelling does not stop the remote script, so this is the only bound.
    timeout = _job(workflow).get("timeout-minutes")
    assert timeout is not None, "an unbounded run can hold the only runner"
    assert 0 < timeout <= 120


def test_every_step_that_names_the_script_names_the_same_one(workflow):
    # Only the run step's path was pinned; a typo in the summary step's copy
    # broke the weekly Box listing with nothing failing.
    # Whole paths, not substrings: `backup.pyy` contains `backup.py`.
    script = "scheduled-jobs/weekly-backup/backup.py"
    found = [
        path
        for step in _job(workflow)["steps"]
        for path in re.findall(r"[\w./-]*backup\.py[\w.]*", step.get("run") or "")
    ]
    assert len(found) >= 2, "the run step and the summary step both invoke it"
    for path in found:
        assert path == script, f"the workflow names {path!r}, not {script!r}"


def test_deploy_paths_come_from_secrets_not_literals(text):
    assert "secrets.PROD_DEPLOY_PATH" in text
    assert "secrets.STAGING_DEPLOY_PATH" in text
    assert "/data/bloom" not in text, "deploy paths differ per host; never hardcode one"


def test_an_empty_deploy_path_fails_rather_than_backing_up_home(text):
    # Without the guard, `cd ''` lands in $HOME and the backup runs against
    # whatever happens to be there. Assert the guard's presence AND run it —
    # asserting the text proves the text is present, not that bash rejects an
    # empty path.
    assert "${DEPLOY_PATH:?" in text

    guard = 'DEPLOY_PATH=""\n: "${DEPLOY_PATH:?PROD_DEPLOY_PATH secret is empty}"\n'
    empty = subprocess.run(["bash", "-c", "set -euo pipefail\n" + guard],
                           capture_output=True, text=True)
    assert empty.returncode != 0, "an empty deploy path must abort the job"
    assert "PROD_DEPLOY_PATH secret is empty" in empty.stderr

    ok = subprocess.run(
        ["bash", "-c", 'set -euo pipefail\nDEPLOY_PATH=/srv/bloom\n'
                       ': "${DEPLOY_PATH:?PROD_DEPLOY_PATH secret is empty}"\necho fine'],
        capture_output=True, text=True)
    assert ok.returncode == 0 and "fine" in ok.stdout


# The same list the script-side guard uses. The summary step runs `rclone lsl`
# against the production Box folder on every job, including failed ones, so this
# file needs the full set rather than the three spellings it used to check.
DESTRUCTIVE_RCLONE_VERBS = (
    "sync", "move", "moveto", "purge", "delete", "deletefile",
    "rmdir", "rmdirs", "cleanup",
)


def test_the_workflow_never_deletes_on_the_remote(text):
    for verb in DESTRUCTIVE_RCLONE_VERBS:
        assert f"rclone {verb}" not in text, (
            f"rclone {verb} in the workflow would delete on Box"
        )
    assert "--min-age" not in text


def test_the_workflow_only_reads_from_box(workflow):
    # Positive form: the one rclone call in this file is the listing.
    verbs = [
        line.split("rclone ")[1].split()[0]
        for step in _job(workflow)["steps"]
        for line in (step.get("run") or "").splitlines()
        if "rclone " in line
    ]
    assert verbs == ["lsl"], f"the workflow runs rclone {verbs}, not just a listing"


def test_the_script_matches_the_verbs_this_file_forbids():
    # One list, not two that drift. Read out of the sibling suite's source
    # rather than imported, since these files are not a package.
    sibling = (REPO_ROOT / "tests/unit/test_weekly_backup.py").read_text()
    block = sibling.split("DESTRUCTIVE_RCLONE_VERBS = (")[1].split(")")[0]
    script_side = set(re.findall(r'"([a-z]+)"', block))
    assert set(DESTRUCTIVE_RCLONE_VERBS) == script_side, (
        "the two lists of destructive rclone verbs have drifted apart"
    )


def test_the_summary_step_cannot_run_out_the_jobs_clock(workflow):
    # It makes two network calls with no timeouts of their own, and the deploy
    # key is removed in the step after it. A hang here means the job hits its
    # own limit and the key is left on a shared runner.
    summary = _step(workflow, "summary")
    timeout = summary.get("timeout-minutes")
    assert timeout is not None, "an unbounded summary step can strand the key"
    assert 0 < timeout < _job(workflow)["timeout-minutes"], (
        "the step's bound must land before the job's, or it does nothing"
    )


def test_the_summary_is_written_even_when_the_backup_fails(workflow):
    summary = _step(workflow, "summary")
    assert "summary" in summary["name"].lower()
    assert summary["if"] == "always()", (
        "a failed backup is exactly the week you need the summary for"
    )
    assert "GITHUB_STEP_SUMMARY" in summary["run"]


def test_the_summary_reports_failure_explicitly(text):
    assert "FAILED" in text
    # It must not claim nothing was uploaded: a run that dies between the two
    # artifacts leaves a partial dated folder that the listing below will show.
    assert "No backup was taken this run" not in text
    assert "incomplete dated folder" in text


def test_the_box_listing_does_not_reparse_the_env_file(text):
    # A second env-file parser in shell drifts from load_env_file: `cut -d= -f2`
    # keeps quotes, truncates values containing '=', and takes every match.
    assert "cut -d=" not in text
    assert "--print-destination" in text, "ask the script for its destination"


def test_the_job_runs_on_the_salk_runner(workflow):
    assert _job(workflow)["runs-on"] == ["self-hosted", "linux", "salk-network"]


def test_permissions_are_minimal(workflow):
    assert workflow["permissions"] == {"contents": "read"}
