"""Issue #634: deploy.yml never reloads Kong on kong.yml-only changes.

Mirrors the existing `caddyfile_changed` / `Reload Caddy config` / `Caddy
crash-loop check` pattern in `.github/workflows/deploy.yml`, but for Kong:
a `kongfile_changed` diff output, a `Restart Kong config` step (a full
`docker compose restart kong`, not `kong reload` — see
`openspec/changes/archive/2026-08-09-fix-kong-reload-on-deploy/design.md` Decision 1), and a
delta-based `Kong crash-loop check` step delegating to
`scripts/check_kong_restart_delta.sh` (Decision 2/3).

Also pins Caddy's existing (previously untested) reload/crash-loop shape,
since `deploy-config-reload`'s new spec formally documents that behavior
for the first time alongside the new Kong requirements.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"

# See test_check_kong_restart_delta_script.py's identical helper for why
# this is needed: `bash` can resolve to the WSL launcher shim rather than a
# real POSIX shell on some Windows dev machines, depending on which
# process's PATH is being searched.
_GIT_BASH_CANDIDATES = [
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files\Git\usr\bin\bash.exe",
]


def _bash_executable() -> str:
    for candidate in _GIT_BASH_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return shutil.which("bash") or "bash"


BASH = _bash_executable()


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _run_text(step: dict) -> str:
    return str(step.get("run") or "")


def _find_step_index(steps: list[dict], predicate) -> int | None:
    for idx, step in enumerate(steps):
        if predicate(step):
            return idx
    return None


# (job name, pull step id, restart step id, compose command prefix, secret prefix)
JOB_CASES = [
    (
        "deploy-production",
        "pull_prod",
        "restart_kong_prod",
        "docker compose -f docker-compose.prod.yml --env-file .env.prod",
        "PROD",
    ),
    (
        "deploy-staging",
        "pull_staging",
        "restart_kong_staging",
        "docker compose -p bloom_v2_staging -f docker-compose.prod.yml --env-file .env.staging",
        "STAGING",
    ),
]

SECRET_ENV_VARS = [
    "ANON_KEY",
    "SERVICE_ROLE_KEY",
    "BLOOM_AGENT_KEY",
    "DASHBOARD_USERNAME",
    "DASHBOARD_PASSWORD",
]


def _steps_for(workflow: dict, job_name: str) -> list[dict]:
    return workflow["jobs"][job_name]["steps"]


class TestKongfileChangedDetection:
    @pytest.mark.parametrize("job_name,pull_id,_restart_id,_compose,_prefix", JOB_CASES)
    def test_pull_step_diffs_kong_yml_after_caddyfile(
        self, job_name, pull_id, _restart_id, _compose, _prefix
    ):
        workflow = _load(DEPLOY_YML)
        steps = _steps_for(workflow, job_name)
        pull_step = next(s for s in steps if s.get("id") == pull_id)
        run = _run_text(pull_step)
        assert "volumes/api/kong.yml" in run, (
            f"{job_name}'s Pull latest code step doesn't diff volumes/api/kong.yml"
        )
        assert "KONGFILE_CHANGED=" in run
        caddy_idx = run.index("CADDYFILE_CHANGED=")
        kong_idx = run.index("KONGFILE_CHANGED=")
        assert caddy_idx < kong_idx, (
            "KONGFILE_CHANGED diff must come after CADDYFILE_CHANGED (reuses "
            "the same BEFORE/AFTER already computed for Caddy)"
        )
        assert "kongfile_changed=" in run and "GITHUB_OUTPUT" in run

    @pytest.mark.parametrize("job_name,pull_id,_restart_id,_compose,_prefix", JOB_CASES)
    def test_kongfile_changed_defaults_true_like_caddyfile_changed(
        self, job_name, pull_id, _restart_id, _compose, _prefix
    ):
        workflow = _load(DEPLOY_YML)
        steps = _steps_for(workflow, job_name)
        pull_step = next(s for s in steps if s.get("id") == pull_id)
        run = _run_text(pull_step)
        assert re.search(r"kongfile_changed=\$\{[a-zA-Z_]+:-true\}", run), (
            "kongfile_changed must default to true if the marker line is "
            "ever missing, mirroring caddyfile_changed's fail-safe"
        )


class TestRestartKongConfigStep:
    @pytest.mark.parametrize("job_name,pull_id,restart_id,compose,_prefix", JOB_CASES)
    def test_restart_step_exists_and_gated(self, job_name, pull_id, restart_id, compose, _prefix):
        workflow = _load(DEPLOY_YML)
        steps = _steps_for(workflow, job_name)
        idx = _find_step_index(steps, lambda s: s.get("id") == restart_id)
        assert idx is not None, f"{job_name} is missing a step with id: {restart_id}"
        step = steps[idx]
        assert step.get("if") == f"steps.{pull_id}.outputs.kongfile_changed == 'true'"

    @pytest.mark.parametrize("job_name,pull_id,restart_id,compose,_prefix", JOB_CASES)
    def test_restart_step_positioned_after_caddy_crash_loop_check(
        self, job_name, pull_id, restart_id, compose, _prefix
    ):
        workflow = _load(DEPLOY_YML)
        steps = _steps_for(workflow, job_name)
        caddy_crash_idx = _find_step_index(
            steps, lambda s: s.get("name") == "Caddy crash-loop check"
        )
        restart_idx = _find_step_index(steps, lambda s: s.get("id") == restart_id)
        assert caddy_crash_idx is not None
        assert restart_idx is not None
        assert caddy_crash_idx < restart_idx

    @pytest.mark.parametrize("job_name,pull_id,restart_id,compose,_prefix", JOB_CASES)
    def test_restart_step_uses_full_restart_not_kong_reload(
        self, job_name, pull_id, restart_id, compose, _prefix
    ):
        workflow = _load(DEPLOY_YML)
        steps = _steps_for(workflow, job_name)
        step = next(s for s in steps if s.get("id") == restart_id)
        run = _run_text(step)
        assert f"{compose} restart kong --timeout 10" in run
        assert "kong reload" not in run

    @pytest.mark.parametrize("job_name,pull_id,restart_id,compose,_prefix", JOB_CASES)
    def test_restart_step_guards_missing_container(
        self, job_name, pull_id, restart_id, compose, _prefix
    ):
        workflow = _load(DEPLOY_YML)
        steps = _steps_for(workflow, job_name)
        step = next(s for s in steps if s.get("id") == restart_id)
        run = _run_text(step)
        assert f"{compose} ps -q kong" in run
        assert re.search(r"-z[^\n]*cid[^\n]*\]", run), (
            "restart step must guard on an empty container id before proceeding"
        )

    @pytest.mark.parametrize("job_name,pull_id,restart_id,compose,_prefix", JOB_CASES)
    def test_restart_step_captures_before_restart_count_output(
        self, job_name, pull_id, restart_id, compose, _prefix
    ):
        workflow = _load(DEPLOY_YML)
        steps = _steps_for(workflow, job_name)
        step = next(s for s in steps if s.get("id") == restart_id)
        run = _run_text(step)
        assert "RestartCount" in run
        assert "before_restart_count=" in run and "GITHUB_OUTPUT" in run

    @pytest.mark.parametrize("job_name,pull_id,restart_id,compose,_prefix", JOB_CASES)
    def test_restart_step_polls_health_via_wait_for_kong_healthy_script(
        self, job_name, pull_id, restart_id, compose, _prefix
    ):
        """The health-poll loop is extracted into scripts/wait_for_kong_healthy.sh
        (round-3 review: it was previously inlined identically four times —
        here and in the rollback step, for both prod and staging — with zero
        execution-level test coverage, an asymmetry with
        check_kong_restart_delta.sh, which was specifically extracted for
        testability). See test_wait_for_kong_healthy_script.py for the
        script's own behavioral tests."""
        workflow = _load(DEPLOY_YML)
        steps = _steps_for(workflow, job_name)
        step = next(s for s in steps if s.get("id") == restart_id)
        run = _run_text(step)
        assert "scripts/wait_for_kong_healthy.sh" in run
        assert re.search(r"wait_for_kong_healthy\.sh[^\n]*\b120\b", run), (
            "must pass the 120s bound explicitly (design.md Decision 5), not rely on a default"
        )
        assert "45" not in run, "must not regress to the rejected 45s timeout value"
        assert not re.search(r"\bsleep 5\b", run), (
            "must not use a flat `sleep 5` — poll for health instead (design.md Decision 5)"
        )

    @pytest.mark.parametrize("job_name,pull_id,restart_id,compose,_prefix", JOB_CASES)
    def test_restart_step_wraps_restart_in_external_timeout(
        self, job_name, pull_id, restart_id, compose, _prefix
    ):
        """Round-1 review added an external `timeout 60` around the restart
        command (docker's own `--timeout 10` only bounds the graceful-stop
        grace period, not the whole command's wall time if the daemon itself
        hangs) — but no test pinned its presence, so a future edit could
        silently drop the wrapper and every existing test would still pass."""
        workflow = _load(DEPLOY_YML)
        steps = _steps_for(workflow, job_name)
        step = next(s for s in steps if s.get("id") == restart_id)
        run = _run_text(step)
        assert f"timeout 60 {compose} restart kong --timeout 10" in run, (
            "the restart command must be wrapped in an external `timeout 60`"
        )


class TestKongCrashLoopCheckStep:
    @pytest.mark.parametrize("job_name,pull_id,restart_id,compose,prefix", JOB_CASES)
    def test_crash_loop_check_immediately_follows_restart_step(
        self, job_name, pull_id, restart_id, compose, prefix
    ):
        workflow = _load(DEPLOY_YML)
        steps = _steps_for(workflow, job_name)
        restart_idx = _find_step_index(steps, lambda s: s.get("id") == restart_id)
        crash_idx = _find_step_index(
            steps, lambda s: s.get("name") == f"Kong crash-loop check ({'production' if prefix == 'PROD' else 'staging'})"
        )
        assert restart_idx is not None
        assert crash_idx is not None, f"{job_name} is missing the Kong crash-loop check step"
        assert crash_idx == restart_idx + 1, (
            "Kong crash-loop check must run IMMEDIATELY after the restart step "
            "(adjacency, not just somewhere later) — the two form an inseparable pair"
        )

    @pytest.mark.parametrize("job_name,pull_id,restart_id,compose,prefix", JOB_CASES)
    def test_crash_loop_check_same_if_condition_as_restart(
        self, job_name, pull_id, restart_id, compose, prefix
    ):
        workflow = _load(DEPLOY_YML)
        steps = _steps_for(workflow, job_name)
        restart_step = next(s for s in steps if s.get("id") == restart_id)
        env_label = "production" if prefix == "PROD" else "staging"
        crash_step = next(
            s for s in steps if s.get("name") == f"Kong crash-loop check ({env_label})"
        )
        assert crash_step.get("if") == restart_step.get("if")

    @pytest.mark.parametrize("job_name,pull_id,restart_id,compose,prefix", JOB_CASES)
    def test_crash_loop_check_masks_all_five_secrets(
        self, job_name, pull_id, restart_id, compose, prefix
    ):
        workflow = _load(DEPLOY_YML)
        steps = _steps_for(workflow, job_name)
        env_label = "production" if prefix == "PROD" else "staging"
        crash_step = next(
            s for s in steps if s.get("name") == f"Kong crash-loop check ({env_label})"
        )
        run = _run_text(crash_step)
        assert run.count("::add-mask::") >= len(SECRET_ENV_VARS), (
            f"expected at least {len(SECRET_ENV_VARS)} ::add-mask:: lines "
            f"(one per secret: {SECRET_ENV_VARS})"
        )
        for var in SECRET_ENV_VARS:
            assert re.search(rf"\^{var}=", run), (
                f"crash-loop check must read {var} from the deployed .env.<env> "
                "file to mask it (mirroring the smoke test's ANON_KEY technique)"
            )

    @pytest.mark.parametrize("job_name,pull_id,restart_id,compose,prefix", JOB_CASES)
    def test_crash_loop_check_guards_secrets_with_colon_question_mark(
        self, job_name, pull_id, restart_id, compose, prefix
    ):
        """Round-1 review added `:?` guards for all five secrets (matching
        the pre-existing smoke-test step's convention), but no test pinned
        their presence — a future edit could silently drop them and every
        existing test would still pass, since the masking test above only
        checks that each var is *read*, not that a missing value would be
        caught."""
        workflow = _load(DEPLOY_YML)
        steps = _steps_for(workflow, job_name)
        env_label = "production" if prefix == "PROD" else "staging"
        crash_step = next(
            s for s in steps if s.get("name") == f"Kong crash-loop check ({env_label})"
        )
        run = _run_text(crash_step)
        env_file = ".env.prod" if prefix == "PROD" else ".env.staging"
        for var in SECRET_ENV_VARS:
            assert re.search(rf"\$\{{{var}:\?missing {var} in {re.escape(env_file)}\}}", run), (
                f"crash-loop check must guard {var} with a `:?` non-empty check "
                f"against {env_file}, not just read it"
            )

    @pytest.mark.parametrize("job_name,pull_id,restart_id,compose,prefix", JOB_CASES)
    def test_crash_loop_check_masks_precede_script_invocation(
        self, job_name, pull_id, restart_id, compose, prefix
    ):
        """The masking test above only counts ::add-mask:: occurrences — it
        would still pass if they were emitted AFTER the log-dumping script
        ran, which would defeat the whole point (Decision 6 exists
        specifically to mask before any log dump can happen). Assert order,
        not just presence."""
        workflow = _load(DEPLOY_YML)
        steps = _steps_for(workflow, job_name)
        env_label = "production" if prefix == "PROD" else "staging"
        crash_step = next(
            s for s in steps if s.get("name") == f"Kong crash-loop check ({env_label})"
        )
        run = _run_text(crash_step)
        last_mask_idx = run.rindex("::add-mask::")
        script_idx = run.index("scripts/check_kong_restart_delta.sh")
        assert last_mask_idx < script_idx, (
            "all ::add-mask:: annotations must precede the script invocation "
            "that may dump kong's logs"
        )

    @pytest.mark.parametrize("job_name,pull_id,restart_id,compose,prefix", JOB_CASES)
    def test_crash_loop_check_invokes_script_with_threshold_2(
        self, job_name, pull_id, restart_id, compose, prefix
    ):
        workflow = _load(DEPLOY_YML)
        steps = _steps_for(workflow, job_name)
        env_label = "production" if prefix == "PROD" else "staging"
        crash_step = next(
            s for s in steps if s.get("name") == f"Kong crash-loop check ({env_label})"
        )
        run = _run_text(crash_step)
        assert "scripts/check_kong_restart_delta.sh" in run
        assert f"steps.{restart_id}.outputs.before_restart_count" in run
        tail = run.split("check_kong_restart_delta.sh", 1)[-1]
        assert re.search(r"\b2\b", tail.splitlines()[0] + tail.splitlines()[1]), (
            "must pass threshold 2"
        )
        assert "--" in tail and compose in tail, (
            "must pass through the job's own compose command prefix after --"
        )


class TestCaddyRegressionPinning:
    """Caddy's reload/crash-loop steps are pre-existing and unchanged by this
    fix, but deploy-config-reload's spec now formally documents them for the
    first time — pin their current shape so a future edit can't silently
    break what the spec now promises."""

    @pytest.mark.parametrize("job_name,pull_id,_restart_id,_compose,_prefix", JOB_CASES)
    def test_caddyfile_changed_output_exists(
        self, job_name, pull_id, _restart_id, _compose, _prefix
    ):
        workflow = _load(DEPLOY_YML)
        steps = _steps_for(workflow, job_name)
        pull_step = next(s for s in steps if s.get("id") == pull_id)
        run = _run_text(pull_step)
        assert "caddyfile_changed=" in run and "GITHUB_OUTPUT" in run

    @pytest.mark.parametrize("job_name,pull_id,_restart_id,compose,_prefix", JOB_CASES)
    def test_caddy_reloads_in_place_not_restart(
        self, job_name, pull_id, _restart_id, compose, _prefix
    ):
        workflow = _load(DEPLOY_YML)
        steps = _steps_for(workflow, job_name)
        env_label = "production" if job_name == "deploy-production" else "staging"
        reload_step = next(
            s for s in steps if s.get("name") == f"Reload Caddy config ({env_label})"
        )
        run = _run_text(reload_step)
        assert f"{compose} exec -T caddy" in run
        assert "caddy reload --config /etc/caddy/Caddyfile" in run
        assert "restart" not in run
        assert "stop" not in run

    @pytest.mark.parametrize("job_name,pull_id,_restart_id,compose,_prefix", JOB_CASES)
    def test_caddy_crash_loop_check_uses_absolute_threshold_unconditionally(
        self, job_name, pull_id, _restart_id, compose, _prefix
    ):
        workflow = _load(DEPLOY_YML)
        steps = _steps_for(workflow, job_name)
        step = next(s for s in steps if s.get("name") == "Caddy crash-loop check")
        assert "if" not in step, "Caddy's crash-loop check runs unconditionally, every deploy"
        run = _run_text(step)
        assert re.search(r'\$\w*restarts\w*\\?"?\s*-gt\s*2', run)


class TestKongfileChangedFailSafeDefault:
    """Behavioral counterpart to the shape test above: actually execute the
    parse-and-default line, don't just assert its literal text — mirrors
    tests/unit/test_pr_checks_workflow_shape.py's technique of extracting one
    real line of shell and running it via subprocess, though not its
    `_first_run_line()` mechanism specifically (kongfile_changed's line isn't
    the first line of the multi-line Pull latest code step, so it's found via
    a targeted regex instead)."""

    @pytest.mark.parametrize("job_name,pull_id,_restart_id,_compose,_prefix", JOB_CASES)
    def test_missing_marker_line_defaults_to_true(
        self, tmp_path, job_name, pull_id, _restart_id, _compose, _prefix
    ):
        workflow = _load(DEPLOY_YML)
        steps = _steps_for(workflow, job_name)
        pull_step = next(s for s in steps if s.get("id") == pull_id)
        run = _run_text(pull_step)
        # Extract just the parse-and-default lines (the part after the SSH
        # heredoc closes) — these run on the GitHub Actions runner itself,
        # not over SSH, so they execute standalone under bash.
        match = re.search(
            r'(\w+)=\$\(grep[^\n]*KONGFILE_CHANGED[^\n]*\n\s*echo "kongfile_changed=\$\{\1:-true\}"[^\n]*',
            run,
        )
        assert match, "could not locate the kongfile_changed parse-and-default lines"
        snippet = match.group(0)
        # Simulate the marker line being absent from a truncated/failed SSH
        # output: grep finds nothing, $changed is empty, default kicks in.
        # Use relative filenames + cwd=tmp_path (not absolute paths) so this
        # works regardless of whether `bash` on PATH resolves to Git Bash,
        # WSL, or a native POSIX shell — each has different path-translation
        # rules for absolute Windows paths, but a relative path + matching
        # cwd is unambiguous everywhere.
        (tmp_path / "pull_out.txt").write_text("no marker here\n")
        snippet = re.sub(r"/tmp/pull_(prod|staging)\.out", "pull_out.txt", snippet)
        # Set GITHUB_OUTPUT inside the script itself rather than via
        # subprocess `env=` — when `bash` on PATH resolves to WSL, Windows
        # env vars aren't forwarded into the WSL environment without
        # WSLENV configuration, so an externally-set env var silently
        # wouldn't exist inside the shell that actually runs the snippet.
        script = f'export GITHUB_OUTPUT="gh_output_capture.txt"\n{snippet}'
        result = subprocess.run(
            [BASH, "-c", script],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 0, result.stderr
        output_content = (tmp_path / "gh_output_capture.txt").read_text()
        assert "kongfile_changed=true" in output_content


class TestRollbackRestoresKongConfig:
    """The whole reason this feature exists is that `up -d` can't detect a
    bind-mounted config's CONTENT changing back — which is exactly what
    `Rollback on failure`'s own `git reset --hard` + `up -d` does to
    kong.yml. If the forward path already restarted Kong onto the new
    config before a later step failed, rollback must explicitly restart
    Kong again onto the reverted config, or it silently reintroduces the
    bug this PR fixes."""

    @pytest.mark.parametrize("job_name,pull_id,_restart_id,compose,_prefix", JOB_CASES)
    def test_rollback_restarts_kong_when_kongfile_changed(
        self, job_name, pull_id, _restart_id, compose, _prefix
    ):
        workflow = _load(DEPLOY_YML)
        steps = _steps_for(workflow, job_name)
        rollback_step = next(s for s in steps if s.get("name") == "Rollback on failure")
        run = _run_text(rollback_step)
        assert f"steps.{pull_id}.outputs.kongfile_changed" in run, (
            f"{job_name}'s Rollback on failure step must check kongfile_changed "
            "before restoring kong, or a rollback after a successful Kong "
            "restart leaves Kong serving the failed deploy's config forever"
        )
        assert f"{compose} restart kong" in run
        assert "scripts/wait_for_kong_healthy.sh" in run
        assert re.search(r'wait_for_kong_healthy\.sh \\"\\\$kong_cid\\" 120', run), (
            "rollback must pass the resolved kong_cid and the 120s bound "
            "explicitly, mirroring the forward-path invocation"
        )

    @pytest.mark.parametrize("job_name,pull_id,_restart_id,compose,_prefix", JOB_CASES)
    def test_rollback_kong_restart_is_positioned_after_up_d(
        self, job_name, pull_id, _restart_id, compose, _prefix
    ):
        workflow = _load(DEPLOY_YML)
        steps = _steps_for(workflow, job_name)
        rollback_step = next(s for s in steps if s.get("name") == "Rollback on failure")
        run = _run_text(rollback_step)
        up_d_idx = run.index("up -d --build --remove-orphans --wait")
        kong_restart_idx = run.index("restarting kong so the reverted config takes effect")
        assert up_d_idx < kong_restart_idx, (
            "the rollback Kong restart must happen after the rollback's own "
            "up -d, not before — restarting Kong before the code/env are "
            "even reverted would restart it onto the still-broken config"
        )

    @pytest.mark.parametrize("job_name,pull_id,_restart_id,compose,_prefix", JOB_CASES)
    def test_rollback_kong_restart_failure_does_not_abort_rollback(
        self, job_name, pull_id, _restart_id, compose, _prefix
    ):
        """The rollback SSH block runs under `set -e`. The critical work
        (git reset + up -d) has already succeeded by the time this Kong
        restart runs — a hiccup restarting Kong is a secondary, best-effort
        concern, not a reason to make the whole rollback step report
        failure and mislead on-call into thinking the actual revert failed.
        The restart command must therefore be guarded (e.g. `elif !
        timeout 60 ... restart kong ...; then <warning>`), not a bare
        command that aborts the script under set -e on failure."""
        workflow = _load(DEPLOY_YML)
        steps = _steps_for(workflow, job_name)
        rollback_step = next(s for s in steps if s.get("name") == "Rollback on failure")
        run = _run_text(rollback_step)
        restart_line = next(
            line for line in run.splitlines() if f"{compose} restart kong" in line
        )
        assert restart_line.strip().startswith(("elif ", "if ")) or " || " in restart_line, (
            f"the rollback Kong restart command must be guarded against its own "
            f"failure aborting the script under set -e, found unguarded: {restart_line!r}"
        )
        assert "kong restart during rollback failed" in run, (
            "a failed rollback restart must warn, not silently abort the step"
        )

    @pytest.mark.parametrize("job_name,pull_id,_restart_id,compose,_prefix", JOB_CASES)
    def test_rollback_warns_and_continues_when_kong_container_not_found(
        self, job_name, pull_id, _restart_id, compose, _prefix
    ):
        """Shape-level pin for the container-not-found branch — spec.md
        requires it, but no test previously asserted its exact text exists."""
        workflow = _load(DEPLOY_YML)
        steps = _steps_for(workflow, job_name)
        rollback_step = next(s for s in steps if s.get("name") == "Rollback on failure")
        run = _run_text(rollback_step)
        assert "::warning::kong container not found during rollback restart — skipping" in run

    @pytest.mark.parametrize("job_name,pull_id,_restart_id,compose,_prefix", JOB_CASES)
    def test_rollback_final_message_gated_on_kong_rollback_ok_flag(
        self, job_name, pull_id, _restart_id, compose, _prefix
    ):
        """BLOCKING fix (round-3 review): the unconditional 'Rollback
        complete — previous version restored and healthy' message used to
        print even when the Kong health-poll above it timed out — the only
        untested branch was an informational status line with no
        ::warning:: and no exit. Now a `kong_rollback_ok` flag, flipped
        false by every bad Kong sub-path (container not found, restart
        failed, health-poll didn't reach healthy), gates which final
        message prints. This is the shape-level half of the fix; see
        TestRollbackKongHealthGating below for the behavioral half."""
        workflow = _load(DEPLOY_YML)
        steps = _steps_for(workflow, job_name)
        rollback_step = next(s for s in steps if s.get("name") == "Rollback on failure")
        run = _run_text(rollback_step)
        assert "kong_rollback_ok=true" in run
        assert re.search(r"kong_rollback_ok=false", run), (
            "at least one bad-path branch must flip the flag to false"
        )
        # `(?: && \[...\])*` tolerates later blocks adding their own conjunct
        # (PR #712 added `caddy_rollback_ok`) while still pinning that kong's is
        # required and that only `&&` — never `||` — may extend the condition.
        assert re.search(
            r'if \[ \\"\\\$kong_rollback_ok\\" = \\"true\\" \](?: && \[[^\]]*\])*; then\s*\n\s*echo \'Rollback complete — previous version restored and healthy\'',
            run,
        ), "the clean success message must be gated behind kong_rollback_ok"
        assert re.search(
            r"else\s*\n\s*echo '::warning::Rollback complete",
            run,
        ), "the non-healthy case must print a ::warning::-prefixed message instead"
        # The health-poll timeout case specifically must flip the flag too
        # (not just container-not-found / restart-command-failure) — this is
        # the exact gap the blocking issue was about.
        health_block = run[run.index("wait_for_kong_healthy.sh") :]
        assert re.search(r'kong_status\\" != \\"healthy', health_block)
        ok_false_after_health = health_block.index(
            "kong_rollback_ok=false", health_block.index('!= \\"healthy')
        )
        assert ok_false_after_health > 0


class TestRollbackKongHealthGating:
    """Behavioral counterpart to the shape tests above: actually execute the
    rollback's Kong-restart-and-health-gate block (extracted from the SSH
    heredoc, GH expression substituted with a literal, docker/timeout/
    wait_for_kong_healthy.sh stubbed), rather than only asserting on its
    literal text. This is what would have caught the BLOCKING round-3 issue
    (rollback claiming 'healthy' when the health-poll actually timed out)
    before it shipped."""

    @staticmethod
    def _extract_snippet(run: str, kongfile_changed_expr: str, kongfile_changed_value: str) -> str:
        start = run.index("kong_rollback_ok=true")
        end_marker = (
            "kong health could not be confirmed (see warning above) — "
            "verify manually on the deploy host'"
        )
        end_marker_idx = run.index(end_marker) + len(end_marker)
        newline_idx = run.index("\n", end_marker_idx)
        fi_idx = run.index("fi", newline_idx) + 2
        snippet = run[start:fi_idx]
        snippet = snippet.replace(kongfile_changed_expr, kongfile_changed_value)
        # The slice also spans the Caddy block that PR #712 added between the Kong
        # block and the summary. Its own expression has to be substituted or bash
        # aborts that line with `bad substitution`, leaving caddy_rollback_ok unset
        # and every summary assertion below reading a branch nobody chose. 'false'
        # keeps Caddy out of the way of these Kong-focused cases.
        snippet = re.sub(r"\$\{\{ steps\.\w+\.outputs\.caddyfile_changed \}\}", "false", snippet)
        assert "${{" not in snippet, f"unsubstituted GitHub expression in snippet: {snippet!r}"
        # Unescape the SSH heredoc's `\$`/`\"` escaping so this runs as
        # standalone bash, exactly as it would on the remote host.
        snippet = snippet.replace('\\"', '"').replace("\\$", "$")
        return snippet

    @staticmethod
    def _install_fakes(tmp_path: Path, kong_cid: str, restart_fails: bool):
        fake_bin = tmp_path / "fakebin"
        fake_bin.mkdir()
        docker_stub = fake_bin / "docker"
        docker_stub.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
if [ "$1" = "compose" ]; then
  shift
  case " $* " in
    *" ps "*"-q"*"kong"*) printf '%s' "{kong_cid}" ;;
    *" restart "*"kong"*) {"exit 1" if restart_fails else "exit 0"} ;;
    *) exit 0 ;;
  esac
  exit 0
fi
exit 0
"""
        )
        timeout_stub = fake_bin / "timeout"
        timeout_stub.write_text('#!/usr/bin/env bash\nshift\nexec "$@"\n')
        for stub in (docker_stub, timeout_stub):
            stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        wait_stub = scripts_dir / "wait_for_kong_healthy.sh"
        wait_stub.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
# Validate the rollback actually passes the container id it just resolved
# and the 120s bound — not just that *some* call happened. Without this, a
# mutation shrinking the rollback path's timeout (e.g. 120 -> 5) or passing
# the wrong variable would go undetected, since a test double that ignores
# its arguments returns the same canned status regardless of input.
if [ "${{1-}}" != "{kong_cid}" ]; then
  echo "fake wait_for_kong_healthy.sh: unexpected container id: ${{1-<missing>}} (expected {kong_cid})" >&2
  exit 98
fi
if [ "${{2-}}" != "120" ]; then
  echo "fake wait_for_kong_healthy.sh: unexpected timeout arg: ${{2-<missing>}} (expected 120)" >&2
  exit 98
fi
printf '%s\\n' "$FAKE_KONG_HEALTH_STATUS"
exit "${{FAKE_KONG_HEALTH_EXIT:-0}}"
"""
        )
        wait_stub.chmod(wait_stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        return fake_bin

    def _run(
        self,
        tmp_path,
        job_name,
        pull_id,
        health_status: str,
        health_exit: str,
        kong_cid: str = "fake-kong-cid",
        restart_fails: bool = False,
    ):
        workflow = _load(DEPLOY_YML)
        steps = _steps_for(workflow, job_name)
        rollback_step = next(s for s in steps if s.get("name") == "Rollback on failure")
        run = _run_text(rollback_step)
        kongfile_changed_expr = f"${{{{ steps.{pull_id}.outputs.kongfile_changed }}}}"
        snippet = self._extract_snippet(run, kongfile_changed_expr, "true")
        fake_bin = self._install_fakes(tmp_path, kong_cid, restart_fails)
        env = {
            **os.environ,
            "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "FAKE_KONG_HEALTH_STATUS": health_status,
            "FAKE_KONG_HEALTH_EXIT": health_exit,
        }
        result = subprocess.run(
            [BASH, "-c", snippet],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )
        assert result.returncode in (0, 1), (
            f"snippet must exit 0 (kong healthy) or 1 (kong_rollback_ok=false), "
            f"not abort some other way: rc={result.returncode} stderr={result.stderr}"
        )
        return result

    @pytest.mark.parametrize("job_name,pull_id,_restart_id,_compose,_prefix", JOB_CASES)
    def test_final_message_is_warning_when_health_poll_times_out(
        self, tmp_path, job_name, pull_id, _restart_id, _compose, _prefix
    ):
        result = self._run(
            tmp_path, job_name, pull_id, health_status="unhealthy", health_exit="1"
        )
        stdout = result.stdout
        assert "::warning::kong did not report healthy" in stdout
        assert "::warning::Rollback complete" in stdout
        assert "Rollback complete — previous version restored and healthy" not in stdout, (
            "must not print the unqualified success message when kong never "
            "reported healthy — this is the exact BLOCKING gap round-3 review found"
        )
        assert result.returncode == 1, (
            "the step itself must fail (not just warn) so on-call scanning "
            "step colors in the Actions UI doesn't miss that kong may still be down"
        )

    @pytest.mark.parametrize("job_name,pull_id,_restart_id,_compose,_prefix", JOB_CASES)
    def test_final_message_is_clean_when_kong_reports_healthy(
        self, tmp_path, job_name, pull_id, _restart_id, _compose, _prefix
    ):
        result = self._run(tmp_path, job_name, pull_id, health_status="healthy", health_exit="0")
        stdout = result.stdout
        assert "Rollback complete — previous version restored and healthy" in stdout
        assert "::warning::kong did not report healthy" not in stdout
        assert "::warning::Rollback complete" not in stdout
        assert result.returncode == 0

    @pytest.mark.parametrize("job_name,pull_id,_restart_id,_compose,_prefix", JOB_CASES)
    def test_final_message_is_warning_when_kong_container_not_found(
        self, tmp_path, job_name, pull_id, _restart_id, _compose, _prefix
    ):
        result = self._run(
            tmp_path,
            job_name,
            pull_id,
            health_status="n/a",
            health_exit="0",
            kong_cid="",
        )
        stdout = result.stdout
        assert "::warning::kong container not found during rollback restart — skipping" in stdout
        assert "::warning::Rollback complete" in stdout
        assert "Rollback complete — previous version restored and healthy" not in stdout
        assert result.returncode == 1

    @pytest.mark.parametrize("job_name,pull_id,_restart_id,_compose,_prefix", JOB_CASES)
    def test_final_message_is_warning_when_restart_command_fails(
        self, tmp_path, job_name, pull_id, _restart_id, _compose, _prefix
    ):
        result = self._run(
            tmp_path,
            job_name,
            pull_id,
            health_status="n/a",
            health_exit="0",
            restart_fails=True,
        )
        stdout = result.stdout
        assert "::warning::kong restart during rollback failed" in stdout
        assert "::warning::Rollback complete" in stdout
        assert "Rollback complete — previous version restored and healthy" not in stdout
        assert result.returncode == 1

    @pytest.mark.parametrize("job_name,pull_id,_restart_id,_compose,_prefix", JOB_CASES)
    def test_health_poll_invoked_with_resolved_container_id_and_120s_bound(
        self, tmp_path, job_name, pull_id, _restart_id, _compose, _prefix
    ):
        """Mutation-testing gap closed: previously the stub ignored its
        arguments entirely, so shrinking the rollback path's timeout (e.g.
        120 -> 5) or passing the wrong container-id variable would go
        undetected by every existing test. The stub now validates both
        arguments and exits non-zero (a distinct code, 98) if either is
        wrong — a passing run here means the real 'fake-kong-cid' and '120'
        were actually passed through."""
        result = self._run(
            tmp_path, job_name, pull_id, health_status="healthy", health_exit="0"
        )
        assert result.returncode == 0, (
            f"stub rejected the health-poll invocation's arguments: {result.stderr}"
        )
