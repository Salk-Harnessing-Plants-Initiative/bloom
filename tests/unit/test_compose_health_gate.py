"""The CI step that waits for services to be healthy must catch broken services.

`docker compose ps --format json` prints its results in one of two ways,
depending on the Docker version: one service per line, or all services together
in one list. The CI step was written for the list version only. Given the
one-per-line version it printed an error instead of naming the broken services,
that error was thrown away, and the step read the resulting silence as "nothing
is unhealthy" and passed.

It passed that way every time, no matter what was actually broken. A broken
healthcheck went through it to staging and blocked deploys for two days.

The tests below take the real commands out of the workflow file and run them
against both versions of the output, so this step cannot go back to passing
whatever it is given. See issue #163.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PR_CHECKS = REPO_ROOT / ".github" / "workflows" / "pr-checks.yml"

GATE_STEP = "Wait for services to be healthy"
SINGLE_SERVICE_WAIT_STEPS = ("Start MinIO and create buckets", "Start database")

# One good service and three bad ones: unhealthy, still starting, and exited with
# an error. The step must notice all three, however Docker prints them.
_SERVICES = [
    {"Name": "db-prod", "Health": "healthy", "State": "running", "ExitCode": 0},
    {"Name": "langchain-agent", "Health": "unhealthy", "State": "running", "ExitCode": 0},
    {"Name": "bloommcp", "Health": "starting", "State": "running", "ExitCode": 0},
    {"Name": "supavisor", "Health": "", "State": "exited", "ExitCode": 1},
]
ONE_PER_LINE = "\n".join(json.dumps(s) for s in _SERVICES)
ONE_BIG_LIST = json.dumps(_SERVICES)

requires_jq = pytest.mark.skipif(
    shutil.which("jq") is None, reason="jq not installed"
)


def _steps() -> list[dict]:
    workflow = yaml.safe_load(PR_CHECKS.read_text(encoding="utf-8"))
    return [
        step
        for job in workflow.get("jobs", {}).values()
        for step in (job.get("steps") or [])
    ]


def _step_run(name: str) -> str:
    matching = [s for s in _steps() if s.get("name") == name]
    assert matching, f"pr-checks.yml has no step named {name!r}"
    return str(matching[0].get("run") or "")


def _jq_programs(run: str, flag: str) -> list[str]:
    """Every `jq <flag> '<program>'` found in a step, in the order written."""
    return re.findall(rf"jq {re.escape(flag)} '([^']+)'", run)


def _run_jq(program: str, stdin: str) -> str:
    result = subprocess.run(
        ["jq", "-r", program],
        input=stdin,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _flagged(docker_output: str) -> tuple[set[str], set[str]]:
    """Run the step's real commands over `docker_output`.

    Returns the service names it flags as (not-yet-healthy, exited-with-error).
    """
    run = _step_run(GATE_STEP)
    convert = _jq_programs(run, "-c")
    assert len(convert) == 1, f"expected one converting jq -c, found {convert}"
    checks = _jq_programs(run, "-r")
    assert len(checks) == 2, f"expected two checking jq -r, found {checks}"

    one_per_line = _run_jq(convert[0], docker_output)
    unhealthy, exited = (
        {n for n in _run_jq(c, one_per_line).splitlines() if n} for c in checks
    )
    return unhealthy, exited


@requires_jq
@pytest.mark.parametrize(
    "docker_output", [ONE_PER_LINE, ONE_BIG_LIST], ids=["one-per-line", "one-big-list"]
)
def test_catches_services_that_are_not_healthy_yet(docker_output: str):
    unhealthy, _ = _flagged(docker_output)
    assert unhealthy == {"langchain-agent", "bloommcp"}, (
        "the step must name every service that is unhealthy or still starting, "
        f"whichever way Docker printed the list. It named: {unhealthy}"
    )


@requires_jq
@pytest.mark.parametrize(
    "docker_output", [ONE_PER_LINE, ONE_BIG_LIST], ids=["one-per-line", "one-big-list"]
)
def test_catches_services_that_exited_with_an_error(docker_output: str):
    _, exited = _flagged(docker_output)
    assert exited == {"supavisor"}, (
        "the step must name every service that exited with an error, whichever "
        f"way Docker printed the list. It named: {exited}"
    )


@requires_jq
def test_says_nothing_when_every_service_is_fine():
    all_good = json.dumps(
        [{"Name": "db-prod", "Health": "healthy", "State": "running", "ExitCode": 0}]
    )
    unhealthy, exited = _flagged(all_good)
    assert not unhealthy and not exited, (
        "a working stack must not be flagged — a check that matches everything "
        f"would block every PR. Got {unhealthy} and {exited}"
    )


def test_error_messages_are_not_thrown_away():
    run = _step_run(GATE_STEP)
    assert "2>/dev/null" not in run and "2> /dev/null" not in run, (
        "this step must not hide error messages. An error here produces no output, "
        "which the step counts as zero broken services and passes. Let it fail."
    )


def test_handles_both_ways_docker_prints_the_service_list():
    run = _step_run(GATE_STEP)
    assert 'if type == "array" then .[] else . end' in run, (
        "the step must handle both ways Docker prints the service list — one per "
        "line, or all of them in one list — before checking anything."
    )
    assert not re.search(r"jq -r '\.\[\]", run), (
        "this only works when Docker prints one big list; it errors out on the "
        "one-service-per-line version. Convert the output first."
    )


def test_fails_when_no_services_are_listed_at_all():
    run = _step_run(GATE_STEP)
    assert re.search(r'if \[ -z "\$PS_JSON" \]', run), (
        "an empty list means nothing started, not that everything is fine — the "
        "step must fail instead of counting zero broken services and passing."
    )


@requires_jq
@pytest.mark.parametrize("step_name", SINGLE_SERVICE_WAIT_STEPS)
def test_single_service_waits_handle_both_ways_too(step_name: str):
    """These two wait on one service each and give up after a timeout, so they
    cannot pass while something is broken. They would still stop working if
    Docker changed how it prints the list."""
    run = _step_run(step_name)
    assert 'if type == \\"array\\" then .[] else . end' in run, (
        f"{step_name!r} reads Docker's service list without handling both ways it "
        "can be printed, so a Docker upgrade would make this wait time out."
    )
