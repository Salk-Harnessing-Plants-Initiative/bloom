"""Regression guard for the migration-failure issue-reopen step in
``.github/workflows/deploy.yml`` (bloom#780 / ``harden-cutover-guard-deploy-gaps``).

This repo's own ``auto-close-issues-on-staging.yml`` closes issues referenced by
closing keywords in a merged PR's title/body, at PR-merge time -- well before
``deploy.yml``'s ``push``-triggered ``deploy-staging``/``deploy-production`` jobs
even run. By the time a migration step actually fails, the issue(s) a PR was
meant to fix are therefore usually already closed (bloom#410, bloom#685). The
step this test covers reopens them and explains why.

Why a unit test at all: this step triggers ONLY on ``push`` to ``staging``/
``main``, gated on a real "Apply database migrations" failure against real
historical data -- it can structurally never run in PR CI, for the same reason
``test_auto_close_workflow_shape.py`` exists for its own sibling workflow. This
test is the only pre-merge gate a regression in it would ever hit.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
AUTO_CLOSE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "auto-close-issues-on-staging.yml"

# Unique substring of the new step's `name:` in both jobs -- used to locate it
# without depending on the "(staging)"/"(production)" suffix.
STEP_NAME_MARKER = "Reopen deploy-gated issues on migration failure"

JOBS = [
    ("deploy-staging", "migrate_staging"),
    ("deploy-production", "migrate_prod"),
]

# Duplicated verbatim from test_auto_close_workflow_shape.py's own KEYWORD_CASES
# (per this change's tasks.md §2.2 -- duplicated rather than imported, so this
# file has no import-path dependency on that one). Keep the two tables in sync
# by hand; a mismatch here is exactly the kind of drift this test exists to
# catch, since both workflows must agree on what counts as a closing reference.
KEYWORD_CASES = [
    ("Closes #5", [5]),
    ("closes #5", [5]),
    ("CLOSES #5", [5]),
    ("Closed #42", [42]),
    ("Close #7", [7]),
    ("Fixes #5", [5]),
    ("Fix #5", [5]),
    ("Fixed #5", [5]),
    ("Resolves #9", [9]),
    ("Resolve #9", [9]),
    ("Resolved #9", [9]),
    ("Closes: #5", [5]),
    ("Fixes #1, closes #2", [1, 2]),
    ("Fixes #1, #2", [1]),
    ("Close #7 and close #8", [7, 8]),
    ("closes #5 closes #5", [5]),
    ("Resolves\n#9", [9]),
    ("title\n\nCloses #12", [12]),
    ("(#5)", []),
    ("see #5 for context", []),
    ("(#315, #305 AC5)", []),
    ("owner/repo#5", []),
    ("see talmolab/sleap-roots-analyze#162", []),
    ("Closes owner/repo#9 and #5", []),
    ("fix bug in #305 handling", []),
    ("This does not directly reference #5", []),
    ("Closes:#5", []),
]


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _job(workflow: dict, job_name: str) -> dict:
    return workflow["jobs"][job_name]


def _find_reopen_step(job: dict) -> dict | None:
    for step in job.get("steps", []):
        if STEP_NAME_MARKER in (step.get("name") or ""):
            return step
    return None


def _script_of(job_name: str) -> str:
    job = _job(_load(WORKFLOW), job_name)
    step = _find_reopen_step(job)
    assert step is not None, (
        f"no step containing {STEP_NAME_MARKER!r} found in job {job_name!r} -- "
        "has it been added to deploy.yml yet?"
    )
    return step["with"]["script"]


def _extract_braced_block(script: str, start_pattern: str) -> str:
    """Return the contents of the first `{ ... }` block whose opening brace
    follows a match of `start_pattern`, matched by brace-depth counting (not a
    fixed-width regex) so this is robust to internal formatting/indentation."""
    m = re.search(start_pattern, script)
    assert m, f"pattern {start_pattern!r} not found in script"
    brace_start = script.index("{", m.end())
    depth = 0
    for i in range(brace_start, len(script)):
        if script[i] == "{":
            depth += 1
        elif script[i] == "}":
            depth -= 1
            if depth == 0:
                return script[brace_start + 1 : i]
    raise AssertionError("unbalanced braces while extracting block")


def _auto_close_regex_source() -> str:
    workflow = _load(AUTO_CLOSE_WORKFLOW)
    script = workflow["jobs"]["close-referenced-issues"]["steps"][0]["with"]["script"]
    match = re.search(r"const re = /(.+)/gi;", script)
    assert match, "auto-close-issues-on-staging.yml's closing-keyword regex not found"
    return match.group(1)


# ---------------------------------------------------------------------------
# Shape invariants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("job_name, step_id", JOBS)
def test_permissions_block_is_least_privilege(job_name: str, step_id: str) -> None:
    job = _job(_load(WORKFLOW), job_name)
    perms = job.get("permissions") or {}
    assert perms.get("contents") == "read", (
        f"{job_name}: expected contents: read, got {perms.get('contents')!r}"
    )
    assert perms.get("issues") == "write", (
        f"{job_name}: expected issues: write, got {perms.get('issues')!r}"
    )
    assert perms.get("pull-requests") == "read", (
        f"{job_name}: expected pull-requests: read (needed for commits/{{sha}}/pulls "
        f"and pulls.get), got {perms.get('pull-requests')!r}"
    )


@pytest.mark.parametrize("job_name, step_id", JOBS)
def test_reopen_step_exists_gated_on_migration_failure_exactly(
    job_name: str, step_id: str
) -> None:
    job = _job(_load(WORKFLOW), job_name)
    step = _find_reopen_step(job)
    assert step is not None, f"no '{STEP_NAME_MARKER}' step found in {job_name}"
    cond = str(step.get("if", ""))
    expected = f"failure() && steps.{step_id}.outcome == 'failure'"
    assert cond == expected, (
        f"{job_name}: expected if: {expected!r} exactly, got {cond!r} -- a bare "
        "outcome check (no `failure() &&`) is implicitly ANDed with success(), "
        "which is already false once the migration step has failed, so the step "
        "would never run (the round-1 bug in this change's own design history)"
    )


@pytest.mark.parametrize(
    "outcome, expected",
    [("failure", True), ("skipped", False), ("success", False)],
)
@pytest.mark.parametrize("job_name, step_id", JOBS)
def test_condition_is_true_only_for_a_failure_outcome(
    job_name: str, step_id: str, outcome: str, expected: bool
) -> None:
    """The exact-string `if:` (asserted above) is itself the skip-vs-fail guard:
    directly confirm it evaluates true only for `outcome: 'failure'`, not for
    `'skipped'` (0 pending migrations -- a common, correct, non-error state) or
    `'success'`. This is a plain Python string/boolean check on the parsed
    condition, not a GitHub Actions expression evaluator."""
    job = _job(_load(WORKFLOW), job_name)
    step = _find_reopen_step(job)
    assert step is not None
    cond = str(step.get("if", ""))
    match = re.fullmatch(r"failure\(\) && steps\.([\w-]+)\.outcome == '(\w+)'", cond)
    assert match, f"if: condition {cond!r} does not match the expected exact shape"
    matched_step_id, matched_outcome_literal = match.group(1), match.group(2)
    assert matched_step_id == step_id
    actual = outcome == matched_outcome_literal
    assert actual is expected, (
        f"for outcome={outcome!r}, expected condition truth {expected}, got {actual}"
    )


@pytest.mark.parametrize("job_name, step_id", JOBS)
def test_step_uses_a_sha_pinned_github_script_action(job_name: str, step_id: str) -> None:
    script_job = _job(_load(WORKFLOW), job_name)
    step = _find_reopen_step(script_job)
    assert step is not None
    uses = step.get("uses", "")
    assert uses.startswith("actions/github-script@"), (
        f"{job_name}: expected the step to use actions/github-script, got {uses!r}"
    )
    ref = uses.split("@", 1)[1]
    assert re.fullmatch(r"[0-9a-f]{40}", ref), (
        f"{job_name}: actions/github-script must be pinned to a 40-char commit SHA, "
        f"got {ref!r} -- matches this repo's setup-uv / auto-close-issues-on-staging.yml "
        "SHA-pin convention."
    )


@pytest.mark.parametrize("job_name, step_id", JOBS)
def test_regex_is_byte_identical_to_auto_close_workflow(job_name: str, step_id: str) -> None:
    script = _script_of(job_name)
    match = re.search(r"const re = /(.+)/gi;", script)
    assert match, f"{job_name}: closing-keyword regex not found in the new step's script"
    assert match.group(1) == _auto_close_regex_source(), (
        f"{job_name}: the new step's regex has drifted from "
        "auto-close-issues-on-staging.yml's own -- the two workflows must always agree "
        "on what counts as a closing reference"
    )


@pytest.mark.parametrize("job_name, step_id", JOBS)
def test_script_checks_state_and_state_reason_and_closed_by(
    job_name: str, step_id: str
) -> None:
    """All three conditions are independently load-bearing: a real GitHub issue can
    have state_reason: completed from a HUMAN close (confirmed against real repo
    data: bloom#685 was closed by a human with state_reason: completed) -- a test
    that only checked two of the three would pass an implementation that reopens
    an issue a human closed."""
    script = _script_of(job_name)
    assert "state === 'closed'" in script, (
        f"{job_name}: missing the state === 'closed' guard (open issues must be "
        "left untouched)"
    )
    assert "state_reason === 'completed'" in script, (
        f"{job_name}: missing the state_reason === 'completed' guard"
    )
    assert "closed_by" in script and "github-actions[bot]" in script, (
        f"{job_name}: missing the closed_by.login === 'github-actions[bot]' guard"
    )


@pytest.mark.parametrize("job_name, step_id", JOBS)
def test_script_noops_on_zero_closing_keyword_matches(job_name: str, step_id: str) -> None:
    script = _script_of(job_name)
    assert re.search(r"numbers\.size === 0", script), (
        f"{job_name}: expected a zero-length closing-keyword-match guard, mirroring "
        "auto-close-issues-on-staging.yml's own `numbers.size === 0` branch"
    )


@pytest.mark.parametrize("job_name, step_id", JOBS)
def test_script_guards_zero_or_multiple_associated_prs(job_name: str, step_id: str) -> None:
    script = _script_of(job_name)
    assert re.search(r"\.length\s*!==\s*1", script), (
        f"{job_name}: expected a `.length !== 1` guard on the commits/{{sha}}/pulls "
        "result, so an ambiguous or missing PR association is logged and skipped "
        "rather than guessed at"
    )


@pytest.mark.parametrize("job_name, step_id", JOBS)
def test_script_skips_a_matched_number_that_is_a_pr_not_an_issue(
    job_name: str, step_id: str
) -> None:
    script = _script_of(job_name)
    assert "pull_request" in script, (
        f"{job_name}: expected a check that skips a matched number whose referent "
        "is itself a PR, mirroring auto-close-issues-on-staging.yml:87-90"
    )


@pytest.mark.parametrize("job_name, step_id", JOBS)
def test_script_has_two_distinct_try_catch_regions(job_name: str, step_id: str) -> None:
    script = _script_of(job_name)
    count = script.count("try {")
    assert count >= 2, (
        f"{job_name}: expected at least two try/catch regions (a top-level one "
        f"around PR resolution, and a per-issue one inside the loop), found {count}"
    )


@pytest.mark.parametrize("job_name, step_id", JOBS)
def test_per_issue_loop_never_returns_or_breaks(job_name: str, step_id: str) -> None:
    """Shape proxy for the mixed-multi-issue-outcome scenario: a live two-issue
    integration test isn't feasible in PR CI for the same reason nothing else
    here is, so this asserts the structural property that guarantees it instead
    -- one issue's skip or reopen outcome cannot affect any other issue's,
    because the loop body never exits early."""
    script = _script_of(job_name)
    loop_body = _extract_braced_block(script, r"for\s*\(const \w+ of \w+\)\s*")
    assert "return" not in loop_body, (
        f"{job_name}: the per-issue loop body must never `return` -- only "
        "`continue`/fall-through, so one issue's outcome can't short-circuit "
        "the rest"
    )
    assert "break" not in loop_body, f"{job_name}: the per-issue loop body must never `break`"


@pytest.mark.parametrize("job_name, step_id", JOBS)
def test_comment_names_the_run_and_the_failed_step(job_name: str, step_id: str) -> None:
    script = _script_of(job_name)
    assert "runId" in script or "html_url" in script, (
        f"{job_name}: the reopen comment must reference a run-identifying token "
        "so a human can find the failed deploy"
    )
    assert "outran" in script.lower() or "before deploy" in script.lower(), (
        f"{job_name}: the reopen comment must explain that auto-close outran "
        "deploy verification, not just say 'reopened'"
    )


# ---------------------------------------------------------------------------
# Closing-keyword regex behavior (mirrors test_auto_close_workflow_shape.py's
# own KEYWORD_CASES exactly -- the two workflows must never disagree)
# ---------------------------------------------------------------------------


def _matches(rx: re.Pattern[str], text: str) -> list[int]:
    return sorted({int(n) for n in rx.findall(text)})


@pytest.mark.parametrize("text, expected", KEYWORD_CASES)
@pytest.mark.parametrize("job_name, step_id", JOBS)
def test_regex_matches_the_documented_keyword_cases(
    job_name: str, step_id: str, text: str, expected: list[int]
) -> None:
    script = _script_of(job_name)
    match = re.search(r"const re = /(.+)/gi;", script)
    assert match, f"{job_name}: closing-keyword regex not found"
    rx = re.compile(match.group(1), re.IGNORECASE)
    assert _matches(rx, text) == expected, (
        f"{job_name}: closing-keyword regex mismatch for {text!r}: "
        f"got {_matches(rx, text)}, expected {expected}"
    )
