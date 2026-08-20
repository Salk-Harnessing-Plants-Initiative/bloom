"""Issue #711: `Rollback on failure` reverts caddy/Caddyfile but never reloads Caddy.

The Caddyfile reaches the container as a bind mount and is not baked into the
image (`caddy/Dockerfile` copies only the caddy binary), so `docker compose up -d` has nothing
to detect: no image layer changes, the service definition is untouched, and the
container is not recreated. Only `caddy reload` re-reads the file.

The forward path handles this (PR #467: `caddyfile_changed` gating a
`Reload Caddy config` step). Rollback does not — it restores the file with
`git reset --hard` and leaves the running Caddy on the failed deploy's config.
Disk and runtime then disagree until some unrelated container start resolves it.

Kong's rollback restart is pinned by test_deploy_kong_reload_on_config_change.py;
this is the Caddy half of the same guarantee.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"

ROLLBACK_STEP_NAME = "Rollback on failure"
RELOAD_CMD = "caddy reload --config /etc/caddy/Caddyfile"

# The two deploy jobs are duplicated YAML, not a shared composite action, so
# every assertion runs against both or one copy is free to lose the behaviour.
# (job name, pull step id, compose command prefix)
JOB_CASES = [
    (
        "deploy-production",
        "pull_prod",
        "docker compose -f docker-compose.prod.yml --env-file .env.prod",
    ),
    (
        "deploy-staging",
        "pull_staging",
        "docker compose -p bloom_v2_staging -f docker-compose.prod.yml --env-file .env.staging",
    ),
]


def _rollback_run(job_name: str) -> str:
    workflow = yaml.safe_load(DEPLOY_YML.read_text(encoding="utf-8"))
    steps = workflow["jobs"][job_name]["steps"]
    step = next(s for s in steps if s.get("name") == ROLLBACK_STEP_NAME)
    return str(step.get("run") or "")


@pytest.mark.parametrize("job_name,pull_id,compose", JOB_CASES)
def test_rollback_reloads_caddy_when_caddyfile_changed(job_name, pull_id, compose):
    """Without this, a rollback of a Caddyfile-touching deploy reverts the file
    and leaves Caddy serving the config from the deploy that just failed."""
    run = _rollback_run(job_name)
    assert f"steps.{pull_id}.outputs.caddyfile_changed" in run, (
        f"{job_name}'s {ROLLBACK_STEP_NAME} must gate on caddyfile_changed — the "
        "flag is already computed by the pull step and read by the forward "
        "reload; rollback is the only consumer missing"
    )
    assert f"{compose} exec -T caddy" in run, (
        f"{job_name}'s rollback must reach caddy through its own compose "
        "invocation (project name and env file differ between the two jobs)"
    )
    assert RELOAD_CMD in run, (
        "reverting the file is not enough — only `caddy reload` makes the "
        "running process re-read it"
    )
    assert f'steps.{pull_id}.outputs.caddyfile_changed }}}}\\" = \\"true\\"' in run, (
        "the gate must fire when caddyfile_changed IS true — an inverted "
        "comparison would reload on every deploy that did not touch the Caddyfile"
    )


@pytest.mark.parametrize("job_name,pull_id,compose", JOB_CASES)
def test_rollback_caddy_reload_happens_after_the_revert(job_name, pull_id, compose):
    """Reloading before `git reset --hard` + `up -d` would reload Caddy onto the
    config the rollback is trying to get rid of."""
    run = _rollback_run(job_name)
    up_d_idx = run.index("up -d")
    reload_idx = run.index(RELOAD_CMD)
    assert up_d_idx < reload_idx, (
        "the rollback Caddy reload must come after the rollback's own up -d, "
        "not before"
    )


@pytest.mark.parametrize("job_name,pull_id,compose", JOB_CASES)
def test_rollback_caddy_reload_failure_warns_and_does_not_abort(job_name, pull_id, compose):
    """The rollback SSH block runs under `set -e`, and the critical code/env
    revert has already succeeded by the time the reload runs. A failed reload
    must not turn a successful recovery into a reported failure.

    This is a deliberate divergence from the Kong rollback block, which escalates
    to `exit 1`. Kong's escalation is driven by a post-restart health wait
    (scripts/wait_for_kong_healthy.sh); `caddy reload` has no equivalent gate.
    """
    run = _rollback_run(job_name)
    reload_line = next(line for line in run.splitlines() if RELOAD_CMD in line)
    assert reload_line.strip().startswith("if ! "), (
        "the reload must be negated (`if ! ...; then <warn>`) so the warning sits on the "
        "FAILURE branch. Dropping the `!` still parses and still guards against set -e, "
        f"but warns on success and swallows a real failure: {reload_line!r}"
    )
    assert "caddy reload during rollback failed" in run, (
        "a failed rollback reload must emit a warning naming the consequence"
    )


@pytest.mark.parametrize("job_name,pull_id,compose", JOB_CASES)
def test_rollback_caddy_reload_never_fails_the_step(job_name, pull_id, compose):
    """Guards the divergence from Kong explicitly: no branch of the Caddy reload
    may exit non-zero, or a reload hiccup would misreport a successful revert."""
    run = _rollback_run(job_name)
    lines = run.splitlines()
    gate = next(i for i, line in enumerate(lines) if "caddyfile_changed" in line)
    indent = len(lines[gate]) - len(lines[gate].lstrip())
    # Slice to the `fi` that closes the gate, rather than a fixed character
    # window — the Kong summary's own `exit 1` sits just past this block.
    # Scan to the Kong summary, not just to the gate's own `fi`: an escalation
    # placed one line past that `fi` — the exact shape of the Kong block above —
    # would otherwise sit outside the slice and pass.
    end = next(
        i for i in range(gate + 1, len(lines)) if "kong_rollback_ok" in lines[i] and "if [" in lines[i]
    )
    block = "\n".join(lines[gate:end])
    assert RELOAD_CMD in block, "sliced the wrong block"
    assert "exit 1" not in block, (
        "the rollback Caddy reload block must warn, never exit 1 — the code and "
        "env revert has already succeeded at this point"
    )


@pytest.mark.parametrize("job_name,pull_id,compose", JOB_CASES)
def test_rollback_payload_has_no_unescaped_backticks(job_name, pull_id, compose):
    """The rollback body is one double-quoted `ssh "..."` argument, so a backtick — even
    inside a `#` comment — is command substitution run by the *runner's* bash before ssh is
    invoked. The remote shell never sees the text intact, and the substitution's stderr lands
    in the rollback log at the moment someone is reading it. `bash -n` cannot catch this: the
    result is syntactically valid.
    """
    lines = _rollback_run(job_name).splitlines()
    # Only the payload counts. Above the `ssh` line the same `#` really is a comment to the
    # runner's shell, so backticks there are inert — the repo has several, deliberately.
    payload_start = next(i for i, line in enumerate(lines) if line.lstrip().startswith("ssh -i "))
    offenders = [line for line in lines[payload_start:] if re.search(r"(?<!\\)`", line)]
    assert not offenders, (
        "unescaped backtick inside the ssh payload — escape as \\` or drop it: "
        f"{offenders!r}"
    )
