"""Guards for the staging deploy's approval-queue fix in ``.github/workflows/deploy.yml``.

A staging run waiting for approval holds the shared ``deploy-bloom`` slot, so every
later push used to be cancelled behind it. ``supersede-stale-staging-waits`` cancels
older runs waiting at the staging gate. The staging deploy checks out the run's own
commit, and a preflight step refuses a dirty tree, an unknown commit or a move
backwards before anything on the server changes, so the rollback skips it.

Neither can run in PR CI, so the shape tests pin the YAML and the behaviour tests run
the scripts: the cancel loop against a fake ``gh``, the preflight and checkout steps
against a local bare repo standing in for ``origin``.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"

SUPERSEDE_JOB = "supersede-stale-staging-waits"
STAGING_JOB = "deploy-staging"
PULL_STEP_ID = "pull_staging"
PREFLIGHT_STEP_ID = "preflight_staging"
DEPLOY_REF_EXPR = "${{ github.event_name == 'push' && github.sha || 'origin/staging' }}"
SSH_PREFIX = (
    'ssh -i ~/.ssh/deploy_key ${{ secrets.DEPLOY_USER }}@${{ secrets.DEPLOY_HOST }} "'
)
# The server's shell doesn't inherit the runner's env, so neither does the stand-in.
LOCAL_SHELL = 'env -u DEPLOY_REF -u GITHUB_OUTPUT bash -c "'
DEPLOY_PATH_EXPR = "${{ secrets.STAGING_DEPLOY_PATH }}"
CURRENT_RUN_ID = 500

BASH = shutil.which("bash") or "bash"
needs_jq = pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")


def _jobs() -> dict:
    return yaml.safe_load(DEPLOY_YML.read_text(encoding="utf-8"))["jobs"]


def _step(job: str, *, step_id: str | None = None, index: int | None = None) -> dict:
    steps = _jobs()[job]["steps"]
    if index is not None:
        return steps[index]
    return next(s for s in steps if s.get("id") == step_id)


def _make_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class TestSupersedeJobShape:
    def test_job_is_outside_the_deploy_concurrency_group(self):
        job = _jobs()[SUPERSEDE_JOB]
        assert "concurrency" not in job, (
            "in deploy-bloom it would queue behind the run it cancels"
        )
        assert "needs" not in job

    def test_deploy_does_not_wait_on_it(self):
        assert "needs" not in _jobs()[STAGING_JOB], (
            "a failed cleanup must not skip the deploy"
        )

    def test_permissions_are_actions_write_only(self):
        assert _jobs()[SUPERSEDE_JOB]["permissions"] == {"actions": "write"}

    def test_holds_no_environment_or_secrets(self):
        job = _jobs()[SUPERSEDE_JOB]
        assert "environment" not in job
        assert "secrets." not in json.dumps(job)

    def test_runs_only_from_the_staging_branch(self):
        condition = _jobs()[SUPERSEDE_JOB]["if"]
        assert condition.startswith("github.ref == 'refs/heads/staging' &&")

    def test_environment_match_is_exact(self):
        run = _step(SUPERSEDE_JOB, index=0)["run"]
        assert '[[ "$envs" == "staging" ]]' in run


class TestPullStepShape:
    def _step_names(self) -> list[str]:
        return [s.get("id") or s["name"] for s in _jobs()[STAGING_JOB]["steps"]]

    def test_push_pins_to_the_run_commit(self):
        assert _jobs()[STAGING_JOB]["env"]["DEPLOY_REF"] == DEPLOY_REF_EXPR

    def test_reset_uses_deploy_ref_not_the_branch_tip(self):
        run = _step(STAGING_JOB, step_id=PULL_STEP_ID)["run"]
        assert "git reset --hard '$DEPLOY_REF'" in run
        assert "reset --hard origin/staging" not in run

    def test_preflight_runs_before_anything_touches_the_server(self):
        names = self._step_names()
        assert names.index(PREFLIGHT_STEP_ID) == names.index("Set up SSH") + 1
        assert names.index(PREFLIGHT_STEP_ID) < names.index(
            "Save previous SHA for rollback"
        )
        assert names.index("Save previous SHA for rollback") < names.index(
            "Snapshot existing .env.staging for rollback"
        )

    def test_rollback_skips_runs_the_preflight_stopped(self):
        rollback = next(
            s
            for s in _jobs()[STAGING_JOB]["steps"]
            if s["name"] == "Rollback on failure"
        )
        assert (
            rollback["if"]
            == "failure() && steps.preflight_staging.outcome == 'success'"
        )

    def test_refusals_live_only_in_the_preflight(self):
        run = _step(STAGING_JOB, step_id=PULL_STEP_ID)["run"]
        assert "Working tree is dirty" not in run
        assert "is-ancestor" not in run

    def test_production_pull_is_unchanged(self):
        run = _step("deploy-production", step_id="pull_prod")["run"]
        assert "git reset --hard origin/main" in run


@needs_jq
class TestSupersedeBehaviour:
    """Runs the cancel loop with a fake ``gh`` that applies the real ``--jq`` filters."""

    FAKE_GH = r"""#!/usr/bin/env bash
set -euo pipefail
[ "$1" = api ] || exit 90
shift
method=GET
if [ "$1" = -X ]; then method=$2; shift 2; fi
url=$1; shift
filter=.
while [ $# -gt 0 ]; do
  case "$1" in --jq) filter=$2; shift 2 ;; *) shift ;; esac
done
case "$url" in
  */actions/workflows/deploy.yml/runs\?status=waiting*) jq -r "$filter" "$FIXTURES/runs.json" ;;
  */pending_deployments) id=${url%/pending_deployments}; jq -r "$filter" "$FIXTURES/pending_${id##*/}.json" ;;
  */cancel)
    [ "$method" = POST ] || exit 91
    id=${url%/cancel}; echo "${id##*/}" >> "$FIXTURES/cancelled"
    [ -e "$FIXTURES/cancel_fails" ] && exit 1 || exit 0 ;;
  *) echo "unexpected url $url" >&2; exit 92 ;;
esac
"""

    def _run(
        self,
        tmp_path: Path,
        waiting: dict[int, list[str]],
        *,
        cancel_fails: bool = False,
    ):
        fixtures = tmp_path / "fixtures"
        fixtures.mkdir()
        (fixtures / "runs.json").write_text(
            json.dumps({"workflow_runs": [{"id": i} for i in waiting]})
        )
        for run_id, envs in waiting.items():
            (fixtures / f"pending_{run_id}.json").write_text(
                json.dumps([{"environment": {"name": e}} for e in envs])
            )
        if cancel_fails:
            (fixtures / "cancel_fails").touch()
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(self.FAKE_GH)
        _make_executable(gh)
        env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "FIXTURES": str(fixtures),
            "REPO": "org/repo",
            "RUN_ID": str(CURRENT_RUN_ID),
        }
        result = subprocess.run(
            [BASH, "-e", "-c", _step(SUPERSEDE_JOB, index=0)["run"]],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        cancelled_file = fixtures / "cancelled"
        cancelled = (
            cancelled_file.read_text().split() if cancelled_file.exists() else []
        )
        return result, cancelled

    def test_cancels_older_staging_waits_only(self, tmp_path):
        result, cancelled = self._run(
            tmp_path,
            {
                100: ["staging"],
                200: ["production"],
                300: ["staging", "production"],
                600: ["staging"],
            },
        )
        assert result.returncode == 0, result.stderr
        assert cancelled == ["100"]
        assert "::notice::Cancelled run 100" in result.stdout

    def test_nothing_waiting_is_a_no_op(self, tmp_path):
        result, cancelled = self._run(tmp_path, {})
        assert result.returncode == 0, result.stderr
        assert cancelled == []

    def test_failed_cancel_fails_the_job_after_trying_the_rest(self, tmp_path):
        result, cancelled = self._run(
            tmp_path, {100: ["staging"], 200: ["staging"]}, cancel_fails=True
        )
        assert result.returncode != 0
        assert cancelled == ["100", "200"]
        assert "::error::Could not cancel run 100" in result.stdout
        assert "::error::Could not cancel run 200" in result.stdout


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
class TestDeployStepsBehaviour:
    """Runs the preflight then the checkout step, as the job does, with ``ssh``
    swapped for a local shell that doesn't inherit the runner's env."""

    @pytest.fixture
    def repos(self, tmp_path):
        """``origin`` (bare) with staging at A -> B -> C, and a server clone at A."""
        seed = tmp_path / "seed"
        seed.mkdir()
        _git(seed, "init", "-q", "-b", "staging")
        _git(seed, "config", "user.email", "t@example.com")
        _git(seed, "config", "user.name", "t")
        shas = {}
        for name in ("A", "B", "C"):
            if name == "C":
                (seed / "caddy").mkdir()
                (seed / "caddy" / "Caddyfile").write_text("changed\n")
            shas[name] = self._commit(seed, name)
        origin = tmp_path / "origin.git"
        _git(tmp_path, "clone", "-q", "--bare", str(seed), str(origin))
        server = tmp_path / "server"
        _git(tmp_path, "clone", "-q", "-b", "staging", str(origin), str(server))
        _git(server, "reset", "-q", "--hard", shas["A"])
        return tmp_path, seed, origin, server, shas

    @staticmethod
    def _commit(seed: Path, name: str) -> str:
        (seed / "file.txt").write_text(name)
        _git(seed, "add", "-A")
        _git(seed, "commit", "-q", "-m", name)
        return _git(seed, "rev-parse", "HEAD")

    @staticmethod
    def _run_step(tmp_path: Path, server: Path, step_id: str, deploy_ref: str):
        run = _step(STAGING_JOB, step_id=step_id)["run"]
        assert SSH_PREFIX in run and DEPLOY_PATH_EXPR in run
        script = (
            run.replace(SSH_PREFIX, LOCAL_SHELL)
            .replace(DEPLOY_PATH_EXPR, str(server))
            .replace("/tmp/pull_staging.out", str(tmp_path / "pull.out"))
        )
        assert "${{" not in script
        env = {
            **os.environ,
            "DEPLOY_REF": deploy_ref,
            "GITHUB_OUTPUT": str(tmp_path / "gh_output"),
        }
        return subprocess.run(
            [BASH, "-e", "-c", script],
            env=env,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )

    def _deploy(self, tmp_path: Path, server: Path, deploy_ref: str):
        """(preflight result, checkout result or None if refused, server HEAD, outputs)."""
        preflight = self._run_step(tmp_path, server, PREFLIGHT_STEP_ID, deploy_ref)
        pull = None
        if preflight.returncode == 0:
            pull = self._run_step(tmp_path, server, PULL_STEP_ID, deploy_ref)
        output = tmp_path / "gh_output"
        outputs = output.read_text() if output.exists() else ""
        return preflight, pull, _git(server, "rev-parse", "HEAD"), outputs

    @staticmethod
    def _ok(result) -> None:
        assert result is not None and result.returncode == 0, (
            result and result.stdout + result.stderr
        )

    def test_push_deploys_its_own_commit_not_the_tip(self, repos):
        tmp_path, _seed, _origin, server, shas = repos
        preflight, pull, head, outputs = self._deploy(tmp_path, server, shas["B"])
        self._ok(preflight)
        self._ok(pull)
        assert head == shas["B"]
        assert f"Deployed {shas['B']}" in pull.stdout
        assert "caddyfile_changed=false" in outputs
        assert "kongfile_changed=false" in outputs

    def test_fetches_a_commit_pushed_after_the_last_deploy(self, repos):
        tmp_path, seed, origin, server, _shas = repos
        new = self._commit(seed, "D")
        _git(seed, "push", "-q", str(origin), "staging")
        preflight, pull, head, _ = self._deploy(tmp_path, server, new)
        self._ok(preflight)
        self._ok(pull)
        assert head == new

    def test_reports_caddyfile_changes_between_commits(self, repos):
        tmp_path, _seed, _origin, server, shas = repos
        _preflight, pull, head, outputs = self._deploy(tmp_path, server, shas["C"])
        self._ok(pull)
        assert head == shas["C"]
        assert "caddyfile_changed=true" in outputs

    def test_refuses_to_move_staging_backwards(self, repos):
        tmp_path, _seed, _origin, server, shas = repos
        _git(server, "reset", "-q", "--hard", shas["C"])
        preflight, pull, head, _ = self._deploy(tmp_path, server, shas["B"])
        assert preflight.returncode != 0 and pull is None
        assert head == shas["C"]
        assert "refusing to move staging backwards" in preflight.stdout

    def test_redeploying_the_same_commit_is_allowed(self, repos):
        tmp_path, _seed, _origin, server, shas = repos
        _preflight, pull, head, _ = self._deploy(tmp_path, server, shas["A"])
        self._ok(pull)
        assert head == shas["A"]

    def test_refuses_an_unknown_commit(self, repos):
        tmp_path, _seed, _origin, server, shas = repos
        preflight, pull, head, _ = self._deploy(tmp_path, server, "f" * 40)
        assert preflight.returncode != 0 and pull is None
        assert head == shas["A"]
        assert "not found after fetching staging" in preflight.stdout

    def test_refuses_a_dirty_tree_and_keeps_the_edits(self, repos):
        tmp_path, _seed, _origin, server, shas = repos
        (server / "file.txt").write_text("local edit")
        preflight, pull, head, _ = self._deploy(tmp_path, server, shas["B"])
        assert preflight.returncode != 0 and pull is None
        assert head == shas["A"]
        assert (server / "file.txt").read_text() == "local edit"
        assert "Working tree is dirty" in preflight.stdout

    def test_manual_dispatch_deploys_the_staging_tip(self, repos):
        tmp_path, _seed, _origin, server, shas = repos
        _preflight, pull, head, _ = self._deploy(tmp_path, server, "origin/staging")
        self._ok(pull)
        assert head == shas["C"]

    def test_manual_dispatch_is_exempt_from_the_backwards_check(self, repos):
        tmp_path, seed, origin, server, shas = repos
        _git(server, "reset", "-q", "--hard", shas["C"])
        _git(seed, "push", "-q", "--force", str(origin), f"{shas['B']}:staging")
        _preflight, pull, head, _ = self._deploy(tmp_path, server, "origin/staging")
        self._ok(pull)
        assert head == shas["B"]

    def test_failed_fetch_stops_a_manual_dispatch(self, repos):
        tmp_path, seed, origin, server, shas = repos
        self._commit(seed, "D")
        _git(seed, "push", "-q", str(origin), "staging")
        _git(server, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
        preflight, pull, head, _ = self._deploy(tmp_path, server, "origin/staging")
        assert preflight.returncode != 0 and pull is None
        assert head == shas["A"]
