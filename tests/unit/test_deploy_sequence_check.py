"""Shape of the deploy's "id sequences behind" check (bloom#1022, design D10).

The step runs after "Rollback on failure", so a red check fails the deploy but keeps the
new code: reverting code can't fix a sequence. It has no `if:`, so any earlier failure
skips it and rolls back exactly as before. It can't run in PR CI, so these tests pin the
YAML.
"""

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"

JOBS = {
    "deploy-production": ("production", "failure()"),
    "deploy-staging": ("staging", "failure() && steps.preflight_staging.outcome == 'success'"),
}


def _steps(job: str) -> list[dict]:
    return yaml.safe_load(DEPLOY_YML.read_text(encoding="utf-8"))["jobs"][job]["steps"]


def _index(steps: list[dict], name: str) -> int:
    names = [s.get("name") for s in steps]
    assert names.count(name) == 1, (name, names.count(name))
    return names.index(name)


@pytest.mark.parametrize("job", sorted(JOBS))
def test_check_is_the_last_step_before_cleanup_and_after_rollback(job):
    env, _ = JOBS[job]
    steps = _steps(job)
    check = _index(steps, f"Check id sequences are not behind ({env})")
    assert _index(steps, "Rollback on failure") < check
    assert check == _index(steps, "Cleanup") - 1


@pytest.mark.parametrize("job", sorted(JOBS))
def test_check_has_no_condition_and_a_timeout(job):
    env, _ = JOBS[job]
    step = _steps(job)[_index(_steps(job), f"Check id sequences are not behind ({env})")]
    assert "if" not in step
    assert 0 < int(step["timeout-minutes"]) <= 10


@pytest.mark.parametrize("job", sorted(JOBS))
def test_check_runs_the_sql_file_read_only(job):
    env, _ = JOBS[job]
    run = _steps(job)[_index(_steps(job), f"Check id sequences are not behind ({env})")]["run"]
    for needle in (
        "psql -X -At",
        "ON_ERROR_STOP=1",
        "-U supabase_admin",
        "-e PGOPTIONS=",
        "default_transaction_read_only=on",
        "statement_timeout=",
        "< scripts/sql/sequences_behind.sql",
    ):
        assert needle in run, needle
    assert f"::error title=Id sequence behind its data ({env})::" in run
    assert f"::error title=Sequence check could not run ({env})::" in run


@pytest.mark.parametrize("job", sorted(JOBS))
def test_rollback_condition_is_unchanged(job):
    _, condition = JOBS[job]
    steps = _steps(job)
    assert steps[_index(steps, "Rollback on failure")]["if"] == condition
