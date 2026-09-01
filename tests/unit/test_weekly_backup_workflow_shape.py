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


def test_dispatch_offers_a_dry_run_and_no_environment_choice(workflow):
    # Production is the only target. A staging option would read staging's env
    # file while still resolving the production container, because the compose
    # project name is pinned in docker-compose.prod.yml.
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert "dry_run" in inputs
    assert "environment" not in inputs


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
    "event_name,expected_gate",
    [
        ("schedule", SCHEDULED_ENVIRONMENT),
        ("workflow_dispatch", "production"),
    ],
)
def test_the_approval_gate_truth_table(workflow, event_name, expected_gate):
    """Evaluate the live GitHub expression, both halves, rather than restating it."""

    def evaluate(expression: str) -> str:
        # `A && 'x' || 'y'` — GitHub's ternary idiom. Parse BOTH literals: a
        # regex that reads only the && half turns this into a tautology.
        match = re.fullmatch(
            r"\$\{\{\s*github\.event_name == 'schedule'\s*"
            r"&&\s*'([^']*)'\s*\|\|\s*'([^']*)'\s*\}\}",
            expression.strip(),
        )
        assert match, f"unrecognised ternary: {expression}"
        scheduled, dispatched = match.groups()
        return scheduled if event_name == "schedule" else dispatched

    assert evaluate(_job(workflow)["environment"]) == expected_gate


# --------------------------------------------------------------------------
# Concurrency, secrets, safety
# --------------------------------------------------------------------------


def test_concurrency_is_not_the_shared_deploy_group(workflow):
    group = workflow["concurrency"]["group"]
    assert "deploy-bloom" not in group, (
        "a stuck deploy must not be able to cancel the backup"
    )
    assert workflow["concurrency"]["cancel-in-progress"] is False


def test_deploy_paths_come_from_secrets_not_literals(text):
    assert "secrets.PROD_DEPLOY_PATH" in text
    assert "secrets.STAGING_DEPLOY_PATH" not in text, "production is the only target"
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


def test_the_workflow_never_deletes_on_the_remote(text):
    assert "rclone delete" not in text
    assert "rclone sync" not in text, "sync deletes at the destination"
    assert "--min-age" not in text


def test_the_summary_is_written_even_when_the_backup_fails(workflow):
    summary = _job(workflow)["steps"][-1]
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
