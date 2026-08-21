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

A failed reload stays warn-only — the code and env revert above it has already
succeeded — but it does flip `caddy_rollback_ok`, so the step's closing message
stops claiming `restored and healthy` when the running config is unconfirmed.
The shape tests pin what the YAML says; TestRollbackCaddyReloadBehaviour at the
bottom executes the block and pins what it prints and exits with.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
import yaml

# `bash` can resolve to the WSL launcher shim on some Windows dev machines; see
# the identical helper in test_deploy_kong_reload_on_config_change.py.
_BASH = next(
    (c for c in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe")
     if Path(c).exists()),
    shutil.which("bash") or "bash",
)

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


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _reload_line(run: str) -> str:
    """The line that executes the reload — not the ::error:: echo, which quotes the same
    command as a retry hint. Matching the bare command against the whole step would let the
    echo satisfy assertions about the command that actually runs."""
    return next(
        line for line in run.splitlines()
        if RELOAD_CMD in line and not line.lstrip().startswith("echo")
    )


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
    assert RELOAD_CMD in run, (
        "reverting the file is not enough — only `caddy reload` makes the "
        "running process re-read it"
    )
    reload_line = _reload_line(run)
    assert f"{compose} exec -T caddy" in reload_line, (
        f"{job_name}'s rollback must reach caddy through its OWN compose invocation "
        "(project name and env file differ between the jobs, and both ssh to the same "
        f"host — a crossed invocation reloads the wrong environment): {reload_line!r}"
    )
    assert reload_line.rstrip().endswith(RELOAD_CMD + "; then"), (
        "the reload command must be the whole guarded condition and end exactly there. "
        "A trailing `|| true` makes the failure branch unreachable, and a suffixed "
        f"--config path still contains the expected command as a prefix: {reload_line!r}"
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
    # Nesting the block inside the kongfile_changed gate directly above parses fine and
    # reads fine, but would reload Caddy only on deploys that also changed kong.yml —
    # silently un-fixing #711 for the Caddyfile-only case this PR exists for.
    lines = run.splitlines()
    gate = next(i for i, line in enumerate(lines) if "caddyfile_changed }}" in line)
    top_level = next(i for i, line in enumerate(lines) if line.strip() == "kong_rollback_ok=true")
    assert _indent(lines[gate]) == _indent(lines[top_level]), (
        "the caddy gate must sit at the rollback body's top level, alongside the kong "
        "block — not nested inside kongfile_changed"
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
    payload_start = next(
        i for i, line in enumerate(lines)
        if line.lstrip().startswith("ssh ") and "deploy_key" in line
    )
    offenders = [line for line in lines[payload_start:] if re.search(r"(?<!\\)`", line)]
    assert not offenders, (
        "unescaped backtick inside the ssh payload — escape as \\` or drop it: "
        f"{offenders!r}"
    )


def _summary_bounds(run: str) -> tuple[list[str], int, int]:
    """(lines, first line of the final summary `if`, its closing `fi`)."""
    lines = run.splitlines()
    start = next(
        i for i, line in enumerate(lines)
        if line.strip().startswith("if [") and "kong_rollback_ok" in line
    )
    indent = _indent(lines[start])
    end = next(
        i for i in range(start + 1, len(lines))
        if _indent(lines[i]) == indent and lines[i].strip() == "fi"
    )
    return lines, start, end


def _summary_branches(run: str) -> list[tuple[str, str]]:
    """(condition, body) per branch of the summary `if`, in source order."""
    lines, start, end = _summary_bounds(run)
    indent = _indent(lines[start])
    branches: list[tuple[str, list[str]]] = []
    for line in lines[start:end]:
        if _indent(line) == indent and line.strip().startswith(("if ", "elif ", "else")):
            branches.append((line.strip(), []))
        else:
            branches[-1][1].append(line)
    return [(cond, "\n".join(body)) for cond, body in branches]


@pytest.mark.parametrize("job_name,pull_id,compose", JOB_CASES)
def test_failed_caddy_reload_qualifies_the_rollback_success_message(job_name, pull_id, compose):
    """A failed reload must not leave the step printing an unqualified
    `restored and healthy`. Caddy is the sole TLS terminator, so that message
    would assert exactly what #711 is about — disk reverted, runtime not —
    with a green step and nothing but an annotation to say otherwise."""
    run = _rollback_run(job_name)
    assert "caddy_rollback_ok=true" in run, "the flag must be initialised before the gate"
    lines, _, _ = _summary_bounds(run)
    init = next(i for i, line in enumerate(lines) if line.strip() == "caddy_rollback_ok=true")
    gate = next(i for i, line in enumerate(lines) if "caddyfile_changed }}" in line)
    assert init < gate, (
        "caddy_rollback_ok must be initialised OUTSIDE the caddyfile_changed gate — "
        "initialising inside leaves it unset on the common path, and an unset flag "
        "never equals 'true', so every no-Caddyfile-change rollback would report the "
        "qualified message"
    )
    branches = _summary_branches(run)
    success_cond, success_body = branches[0]
    assert "Rollback complete — previous version restored and healthy" in success_body, (
        f"expected the clean success message in the first branch: {branches!r}"
    )
    assert "caddy_rollback_ok" in success_cond, (
        "the unqualified success message must require caddy_rollback_ok too, not just "
        f"kong's flag: {success_cond!r}"
    )
    assert "kong_rollback_ok" in success_cond, (
        f"...without dropping kong's existing guarantee: {success_cond!r}"
    )
    assert "&&" in success_cond and "||" not in success_cond, (
        f"the two flags must both hold, not either: {success_cond!r}"
    )


@pytest.mark.parametrize("job_name,pull_id,compose", JOB_CASES)
def test_caddy_only_failure_warns_without_failing_the_step(job_name, pull_id, compose):
    """The qualified message is the whole fix — escalating it to `exit 1` would
    reintroduce the thing the warn-only design exists to avoid (a completed
    code/env revert reported as a failed rollback). Kong's branch still exits 1;
    this one must not."""
    run = _rollback_run(job_name)
    branches = _summary_branches(run)
    caddy_only = [
        (cond, body) for cond, body in branches[1:]
        if "caddy" in body and "kong health could not be confirmed" not in body
    ]
    assert len(caddy_only) == 1, (
        f"expected exactly one caddy-only branch between success and the kong else: {branches!r}"
    )
    cond, body = caddy_only[0]
    assert cond.startswith("elif "), f"caddy-only must be an elif, not the else: {cond!r}"
    assert "kong_rollback_ok" in cond, (
        "the caddy-only branch must still require kong to be OK — otherwise a "
        f"kong failure lands here and skips the exit 1 below: {cond!r}"
    )
    assert "exit 1" not in body, (
        "the caddy-only branch must warn without failing the step — the code and env "
        f"revert has already succeeded: {body!r}"
    )
    assert "::warning::Rollback complete" in body, (
        f"the caddy-only branch must annotate, not just echo: {body!r}"
    )
    kong_cond, kong_body = branches[-1]
    assert kong_cond.startswith("else"), f"kong's branch must stay last: {kong_cond!r}"
    assert "exit 1" in kong_body, (
        "kong's escalation must survive the restructuring — it is health-gated and "
        f"still fails the step: {kong_body!r}"
    )


@pytest.mark.parametrize("job_name,pull_id,compose", JOB_CASES)
def test_reload_failure_is_annotated_warning_not_error(job_name, pull_id, compose):
    """No branch of this block fails the step, and the file reserves `::error::`
    for what does (the kong summary, the reset and up -d failures). An
    `::error::` here is backwards to anyone triaging by severity in the
    Annotations tab."""
    run = _rollback_run(job_name)
    assert "::warning::caddy reload during rollback failed" in run, (
        "the failed-reload annotation must be a warning"
    )
    assert "::error::caddy" not in run, (
        "no ::error:: may describe the caddy reload — it never fails the step"
    )


class TestRollbackCaddyReloadBehaviour:
    """Behavioural counterpart to the shape tests above: extract the Caddy block
    and the summary `if` that consumes its flag, substitute the GitHub
    expression, stub docker/timeout, and actually run it. The shape tests pin
    what the YAML says; these pin what a rollback would print and exit with.
    Mirrors TestRollbackKongHealthGating in
    test_deploy_kong_reload_on_config_change.py.
    """

    @staticmethod
    def _snippet(run: str, pull_id: str, caddyfile_changed: str, kong_rollback_ok: str) -> str:
        lines, _summary_start, summary_end = _summary_bounds(run)
        start = next(
            i for i, line in enumerate(lines) if line.strip() == "caddy_rollback_ok=true"
        )
        body = "\n".join(lines[start : summary_end + 1])
        body = body.replace(
            f"${{{{ steps.{pull_id}.outputs.caddyfile_changed }}}}", caddyfile_changed
        )
        # Unescape the ssh payload's `\$`/`\"` so this runs as standalone bash,
        # exactly as the remote shell would see it.
        body = body.replace('\\"', '"').replace("\\$", "$")
        assert "${{" not in body, (
            "an unsubstituted GitHub expression would abort the snippet with "
            f"`bad substitution` and silently skip the branch under test: {body!r}"
        )
        # Kong's own block is upstream of this slice; its verdict is an input here.
        return f"set -e\nkong_rollback_ok={kong_rollback_ok}\n{body}\n"

    @staticmethod
    def _fake_bin(tmp_path: Path, reload_fails: bool) -> Path:
        import stat as _stat

        fake_bin = tmp_path / "fakebin"
        fake_bin.mkdir()
        docker = fake_bin / "docker"
        docker.write_text(
            "#!/usr/bin/env bash\n"
            'printf "%s\\n" "$*" >> "$DOCKER_CALLS"\n'
            f"exit {1 if reload_fails else 0}\n"
        )
        timeout = fake_bin / "timeout"
        timeout.write_text('#!/usr/bin/env bash\nshift\nexec "$@"\n')
        for stub in (docker, timeout):
            stub.chmod(stub.stat().st_mode | _stat.S_IEXEC | _stat.S_IXGRP | _stat.S_IXOTH)
        return fake_bin

    def _run(self, tmp_path, job_name, pull_id, *, caddyfile_changed, kong_ok, reload_fails):
        import os
        import subprocess

        snippet = self._snippet(_rollback_run(job_name), pull_id, caddyfile_changed, kong_ok)
        calls = tmp_path / "docker_calls"
        env = {
            **os.environ,
            "PATH": f"{self._fake_bin(tmp_path, reload_fails)}{os.pathsep}{os.environ.get('PATH', '')}",
            "DOCKER_CALLS": str(calls),
        }
        result = subprocess.run(
            [_BASH, "-c", snippet], cwd=tmp_path, capture_output=True, text=True,
            env=env, timeout=10,
        )
        return result, (calls.read_text(encoding="utf-8") if calls.exists() else "")

    @pytest.mark.parametrize("job_name,pull_id,compose", JOB_CASES)
    def test_successful_reload_reports_a_clean_rollback(self, tmp_path, job_name, pull_id, compose):
        result, calls = self._run(
            tmp_path, job_name, pull_id,
            caddyfile_changed="true", kong_ok="true", reload_fails=False,
        )
        assert result.returncode == 0, result.stderr
        assert "Rollback complete — previous version restored and healthy" in result.stdout
        assert "::warning::" not in result.stdout
        assert f"{compose.removeprefix('docker ')} exec -T caddy {RELOAD_CMD}" in calls, (
            f"the reload must reach caddy through this job's own compose invocation: {calls!r}"
        )

    @pytest.mark.parametrize("job_name,pull_id,compose", JOB_CASES)
    def test_failed_reload_warns_twice_and_still_exits_zero(self, tmp_path, job_name, pull_id, compose):
        """The whole point of the fix: the reload failure reaches the summary
        instead of stopping at an annotation, and the summary stops claiming
        `healthy` — but the step still succeeds, because the code and env revert
        above it did."""
        result, _calls = self._run(
            tmp_path, job_name, pull_id,
            caddyfile_changed="true", kong_ok="true", reload_fails=True,
        )
        assert result.returncode == 0, (
            f"a failed reload must not fail the rollback step: rc={result.returncode} "
            f"stderr={result.stderr}"
        )
        assert "::warning::caddy reload during rollback failed" in result.stdout
        assert "::warning::Rollback complete" in result.stdout
        assert "the caddy reload could not be confirmed" in result.stdout
        assert "restored and healthy" not in result.stdout, (
            "the unqualified success message must not print when the reload failed"
        )

    @pytest.mark.parametrize("job_name,pull_id,compose", JOB_CASES)
    def test_untouched_caddyfile_skips_the_reload_and_reports_clean(
        self, tmp_path, job_name, pull_id, compose
    ):
        """The common case. `caddy_rollback_ok` defaults to true outside the gate,
        so a rollback that never touched the Caddyfile must not inherit the
        qualified message — nor invoke caddy at all."""
        result, calls = self._run(
            tmp_path, job_name, pull_id,
            caddyfile_changed="false", kong_ok="true", reload_fails=True,
        )
        assert result.returncode == 0, result.stderr
        assert "Rollback complete — previous version restored and healthy" in result.stdout
        assert calls == "", f"caddy must not be touched when the Caddyfile did not change: {calls!r}"

    @pytest.mark.parametrize("job_name,pull_id,compose", JOB_CASES)
    def test_kong_failure_still_escalates_when_caddy_also_failed(
        self, tmp_path, job_name, pull_id, compose
    ):
        """Kong's branch is health-gated and keeps its `exit 1`. Adding the Caddy
        branch above it must not divert a kong failure into the warn-only path."""
        result, _calls = self._run(
            tmp_path, job_name, pull_id,
            caddyfile_changed="true", kong_ok="false", reload_fails=True,
        )
        assert result.returncode == 1, (
            f"kong's escalation must survive: rc={result.returncode} stdout={result.stdout}"
        )
        assert "kong health could not be confirmed" in result.stdout
