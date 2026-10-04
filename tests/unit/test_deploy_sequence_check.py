"""Shape of the deploy's "id sequences behind" check (bloom#1022, design D10).

The step runs after "Rollback on failure", so a red check fails the deploy but keeps the
new code: reverting code can't fix a sequence. It has no `if:`, so any earlier failure
skips it and rolls back exactly as before. It can't run in PR CI, so these tests pin the
YAML, and run the step's script with `ssh` replaced by a stand-in.
"""

import os
import re
import shutil
import subprocess
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
        "lock_timeout=",
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


# --------------------------------------------------------------------------- #
# The step's script, executed: ssh replaced by a stand-in that prints the rows
# the query would return (or fails), under both shells a runner might use.
# --------------------------------------------------------------------------- #

BASH = shutil.which("bash") or "bash"
SSH_PREFIX = "ssh -i ~/.ssh/deploy_key ${{ secrets.DEPLOY_USER }}@${{ secrets.DEPLOY_HOST }}"
FAKE_SSH = (
    'fake_ssh() { if [ "${FAKE_RC:-0}" != 0 ]; then return "$FAKE_RC"; fi; '
    'cat "$FAKE_ROWS"; }\n'
)
# GitHub runs a `run:` with no `shell:` as `bash -e {0}`; `shell: bash` adds pipefail.
SHELLS = ([BASH, "-e", "-c"], [BASH, "--noprofile", "--norc", "-eo", "pipefail", "-c"])


def _run_check(job: str, rows: list[str], tmp_path: Path, *, rc: int = 0, shell=SHELLS[0]):
    env, _ = JOBS[job]
    run = _steps(job)[_index(_steps(job), f"Check id sequences are not behind ({env})")]["run"]
    assert run.count(SSH_PREFIX) == 1
    script = FAKE_SSH + run.replace(SSH_PREFIX, "fake_ssh")
    script = re.sub(r"\$\{\{\s*secrets\.\w+\s*\}\}", "SECRET", script)
    rows_file = tmp_path / "rows.txt"
    rows_file.write_bytes("".join(f"{r}\n" for r in rows).encode("utf-8"))
    proc = subprocess.run(
        [*shell, script],
        capture_output=True,
        env={**os.environ, "FAKE_ROWS": str(rows_file), "FAKE_RC": str(rc)},
        timeout=60,
    )
    return env, proc.returncode, proc.stdout.decode("utf-8")


def _errors(stdout: str, title: str) -> list[str]:
    return [line for line in stdout.splitlines() if line.startswith(f"::error title={title}::")]


@pytest.mark.parametrize("job", sorted(JOBS))
@pytest.mark.parametrize("shell", SHELLS, ids=["bash-e", "pipefail"])
def test_step_passes_and_says_so_when_nothing_is_behind(job, shell, tmp_path):
    env, rc, out = _run_check(job, [], tmp_path, shell=shell)
    assert rc == 0
    assert "::error" not in out
    assert "No public id sequence is behind its data." in out


@pytest.mark.parametrize("job", sorted(JOBS))
@pytest.mark.parametrize("count", [3, 12])
def test_step_fails_with_a_count_at_most_nine_rows_and_the_full_list(job, count, tmp_path):
    rows = [f"tbl_{i} | id | {i + 10} | 1" for i in range(count)]
    env, rc, out = _run_check(job, rows, tmp_path)
    assert rc == 1
    errors = _errors(out, f"Id sequence behind its data ({env})")
    assert len(errors) == 1 + min(count, 9)
    assert f"{count} id sequence(s)" in errors[0]
    for row in rows:
        assert f"  behind: {row}" in out.splitlines()


@pytest.mark.parametrize("job", sorted(JOBS))
def test_step_lists_every_row_past_the_pipe_buffer_even_under_pipefail(job, tmp_path):
    rows = [f"tbl_{i} | id | {i + 10} | 1" for i in range(5000)]
    env, rc, out = _run_check(job, rows, tmp_path, shell=SHELLS[1])
    assert rc == 1  # not 141 from SIGPIPE
    assert sum(line.startswith("  behind: ") for line in out.splitlines()) == 5000


@pytest.mark.parametrize("job", sorted(JOBS))
def test_step_reports_could_not_run_when_ssh_or_psql_fails(job, tmp_path):
    env, rc, out = _run_check(job, ["partial | id | 5 | 1"], tmp_path, rc=2)
    assert rc != 0
    assert len(_errors(out, f"Sequence check could not run ({env})")) == 1
    assert not _errors(out, f"Id sequence behind its data ({env})")


@pytest.mark.parametrize("job", sorted(JOBS))
def test_hostile_table_names_cannot_forge_workflow_commands(job, tmp_path):
    rows = ["::stop-commands::token", "x%0A::add-mask::y | id | 5 | 1", "z\r::warning::w | id | 5 | 1"]
    env, rc, out = _run_check(job, rows, tmp_path)
    assert rc == 1
    for line in out.splitlines():
        assert not line.startswith("::") or line.startswith("::error title="), line
    assert "::stop-commands::token" not in [line for line in out.splitlines() if line.startswith("::")]
    assert any("x%250A" in e for e in _errors(out, f"Id sequence behind its data ({env})"))
