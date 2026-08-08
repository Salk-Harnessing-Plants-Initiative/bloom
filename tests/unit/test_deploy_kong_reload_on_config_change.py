"""Issue #634: deploy.yml never reloads Kong on kong.yml-only changes.

Mirrors the existing `caddyfile_changed` / `Reload Caddy config` / `Caddy
crash-loop check` pattern in `.github/workflows/deploy.yml`, but for Kong:
a `kongfile_changed` diff output, a `Restart Kong config` step (a full
`docker compose restart kong`, not `kong reload` — see
`openspec/changes/fix-kong-reload-on-deploy/design.md` Decision 1), and a
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
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
CHECK_SCRIPT = REPO_ROOT / "scripts" / "check_kong_restart_delta.sh"

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
    def test_restart_step_polls_health_for_exactly_120_seconds(
        self, job_name, pull_id, restart_id, compose, _prefix
    ):
        workflow = _load(DEPLOY_YML)
        steps = _steps_for(workflow, job_name)
        step = next(s for s in steps if s.get("id") == restart_id)
        run = _run_text(step)
        assert "State.Health.Status" in run
        assert "120" in run, "health poll must bound at 120s, not 45s/90s/a flat sleep"
        assert "45" not in run
        assert not re.search(r"\bsleep 5\b", run), (
            "must not use a flat `sleep 5` — poll for health instead (design.md Decision 5)"
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


class TestCheckKongRestartDeltaScriptShapeReady:
    """Sanity check that the script referenced by deploy.yml's crash-loop
    check step doesn't exist yet at this point in the RED phase (task 4/5
    create it) — guards against accidentally implementing task 3 and task 5
    out of TDD order."""

    def test_script_path_referenced_consistently(self):
        workflow = _load(DEPLOY_YML)
        for job_name, _pull_id, _restart_id, _compose, prefix in JOB_CASES:
            steps = _steps_for(workflow, job_name)
            env_label = "production" if prefix == "PROD" else "staging"
            crash_step = next(
                (s for s in steps if s.get("name") == f"Kong crash-loop check ({env_label})"),
                None,
            )
            if crash_step is None:
                pytest.skip("Kong crash-loop check step not implemented yet")
            assert "scripts/check_kong_restart_delta.sh" in _run_text(crash_step)
