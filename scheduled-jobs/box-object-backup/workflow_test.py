"""Tests for the cross-file contract between this job and its workflow.

`runlock.SKIP_MARKER` is printed by the Python and grepped by the YAML. That
is a contract spanning two files in two languages with nothing but a comment
holding it together — exactly the kind that rots silently. An earlier version
of this job carried a comment naming a workflow file that did not exist at
all, and nothing noticed.

These assertions are deliberately about *shape*, not behaviour: they cannot
prove the workflow runs, only that it still agrees with the code it drives.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from runlock import SKIP_MARKER

WORKFLOW = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "box-object-backup.yml"
)


@pytest.fixture(scope="module")
def parsed(workflow: str) -> dict:
    """The workflow as YAML, with comments discarded.

    Assertions on raw file text are satisfied by a comment. Every one of these
    survived having the real thing broken while the searched-for string was
    left in a comment above it: the approval gate re-enabled, the skip branch
    deleted entirely. Parsing removes that channel.
    """
    import yaml

    return yaml.safe_load(workflow)


@pytest.fixture(scope="module")
def summary_script(parsed: dict) -> str:
    steps = parsed["jobs"]["mirror"]["steps"]
    return next(s["run"] for s in steps if s.get("name", "").startswith("Write the run summary"))


def _strip_comments(script: str) -> str:
    """Executable lines only — a comment must not satisfy an assertion."""
    return "\n".join(
        line for line in script.splitlines() if not line.lstrip().startswith("#")
    )


@pytest.fixture(scope="module")
def workflow() -> str:
    if not WORKFLOW.exists():
        pytest.fail(
            f"{WORKFLOW} is missing. runlock.SKIP_MARKER documents a contract "
            "with this file; if the scheduling approach changed, update that "
            "comment too."
        )
    return WORKFLOW.read_text(encoding="utf-8")


class TestSkipMarkerContract:
    """The one string that has to match across the two files."""

    def test_the_workflow_greps_a_prefix_of_the_marker(self, summary_script: str):
        # The YAML greps a plain ASCII prefix: the full marker carries an
        # em dash, which is a poor thing to put in a shell string literal.
        prefix = "box-object-backup: SKIPPED"
        assert SKIP_MARKER.startswith(prefix)
        assert prefix in _strip_comments(summary_script)

    def test_the_python_actually_prints_the_marker(self):
        # The contract was only ever tested from the YAML side; removing the
        # logger.warning that emits it went unnoticed.
        source = (Path(__file__).parent / "backup_objects.py").read_text()
        executable = _strip_comments(source)
        assert "SKIP_MARKER" in executable

    def test_a_stood_down_run_is_not_reported_as_success(self, summary_script: str):
        # The whole point: a skipped run exits 0 exactly as a good one does,
        # so the summary must distinguish them or a months-long gap in the
        # mirror reads as months of green ticks.
        #
        # Comments stripped first — deleting this branch entirely used to pass,
        # because the marker survived in the comment above it.
        script = _strip_comments(summary_script)
        skipped_at = script.find("box-object-backup: SKIPPED")
        succeeded_at = script.find("**succeeded**")
        assert skipped_at != -1, "the skip branch is gone from the summary"
        assert succeeded_at != -1
        assert skipped_at < succeeded_at, (
            "the skip check must be tested before the success branch, "
            "or a stood-down run reports as succeeded"
        )


class TestScheduleShape:
    def test_runs_before_the_postgres_dump(self, workflow: str):
        # Saturday (day 6). The Postgres dump is Sunday (day 0), and objects
        # must land first so every row in that dump has bytes behind it.
        assert 'cron: "17 2 * * 6"' in workflow

    def test_does_not_share_the_deploy_concurrency_group(self, workflow: str):
        # A stuck deploy must not cancel the mirror, and vice versa.
        assert "group: box-object-backup-" in workflow
        assert "group: deploy-bloom" not in workflow

    def test_the_scheduled_run_is_not_behind_an_approval_gate(self, parsed: dict):
        # An unattended 02:17 run routed through a reviewer gate waits for an
        # approval nobody is awake to give, and the backup silently never runs.
        #
        # Read off the parsed job: setting `environment: production` while
        # leaving the ungated name in a comment used to pass.
        environment = parsed["jobs"]["mirror"]["environment"]
        assert "production-scheduled-backup" in environment
        assert "schedule" in environment, (
            "the gate must be chosen by event type, or the scheduled run "
            "inherits production's required reviewer"
        )


class TestRunInvocation:
    def test_every_scheduled_run_verifies(self, workflow: str):
        # --verify defaults to 0, so leaving it off means no scheduled run
        # ever checks that what it copied is actually on Box.
        #
        # Assert the INVOCATION, not the bare flag name: `--verify` also
        # appears in this file's comments and in the dispatch input's
        # description, so `"--verify" in workflow` stays true even after the
        # flag is dropped from the command. Verified by deleting it — that
        # weaker assertion did not go red.
        assert '--verify "$verify"' in workflow

    def test_the_seed_is_not_run_by_the_workflow(self, workflow: str):
        # --full is the multi-day pass. The self-hosted runner is shared with
        # deploys and must not be held for days; the seed runs detached on the
        # host and this workflow stands down against its lock.
        assert "--full" not in workflow

    def test_the_deploy_path_comes_from_a_secret(self, workflow: str):
        # Never a hardcoded path: an earlier installer assumed /data/bloom,
        # which is not where this repo deploys.
        assert "secrets.PROD_DEPLOY_PATH" in workflow
        assert "/data/bloom" not in workflow


class TestSupersededSchedulingIsGone:
    """The systemd approach was reversed; its files must not come back."""

    @pytest.mark.parametrize(
        "leftover",
        [
            "bloom-box-object-backup.service",
            "bloom-box-object-backup.timer",
            "install.sh",
        ],
    )
    def test_the_systemd_files_are_not_present(self, leftover: str):
        assert not (Path(__file__).parent / leftover).exists(), (
            f"{leftover} belongs to the superseded systemd design — shipping it "
            "alongside the workflow tells an operator to install infrastructure "
            "the team decided against"
        )


class TestTheRemoteRunGetsItsConfiguration:
    """The job reads every setting from the environment, and `ssh host cmd`
    supplies none — no profile, no .env file.

    The systemd unit this replaced carried `EnvironmentFile=`. Deleting it
    removed the only mechanism feeding BACKUP_*, POSTGRES_* and MINIO_ROOT_* to
    the process, and nothing replaced it: every scheduled run would have died
    at the first config lookup, after a full scan of storage.objects.
    """

    def run_step(self, workflow: str) -> str:
        import yaml

        parsed = yaml.safe_load(workflow)
        steps = parsed["jobs"]["mirror"]["steps"]
        return next(s["run"] for s in steps if s.get("id") == "run")

    def test_the_env_file_is_read_on_the_remote(self, workflow: str):
        script = self.run_step(workflow)
        assert ".env.$env_name" in script or '.env.$ENV_NAME' in script, (
            "nothing reads the deploy env file, so the job runs with no config"
        )

    def test_every_variable_family_the_job_needs_is_exported(self, workflow: str):
        script = self.run_step(workflow)
        for family in ("BACKUP_", "POSTGRES_", "MINIO_ROOT_"):
            assert family in script, f"{family}* never reaches the process"

    def test_secrets_the_job_does_not_need_are_left_behind(self, workflow: str):
        # Assert the FILTER, not the absence of a word from the file — the
        # script's own comment names JWT as the thing being excluded, so a
        # substring check on the whole script fails for the wrong reason.
        import re

        script = self.run_step(workflow)
        assert "set -a" not in script, "sourcing exports every secret in the file"
        pattern = re.search(r"grep -E '(\^\([^']+)'", script)
        assert pattern, "no filter found — the whole env file would be exported"
        families = pattern.group(1)
        assert "BACKUP_" in families and "MINIO_ROOT_" in families
        for unwanted in ("JWT", "SERVICE_ROLE", "ANON_KEY", "ENC_KEY", "PASSWORD)"):
            assert unwanted not in families, f"filter would export {unwanted}"

    def test_the_env_file_is_assigned_rather_than_sourced(self, workflow: str):
        # `. file` parses a .env as shell — a password containing a quote or a
        # backtick then either aborts the run or executes part of itself.
        script = self.run_step(workflow)
        assert 'export "$key=$value"' in script


class TestDispatchInputCannotReachTheRemoteShell:
    """`verify` is free-text; GitHub validates it no further than "is a string".

    Interpolated into the ssh command string it was executable on the deploy
    host: `50'; <command>; echo '` closed the quote and the remote shell ran
    the rest. argparse's type=int never sees it — the shell splits first.
    """

    def run_step(self, workflow: str) -> str:
        import yaml

        parsed = yaml.safe_load(workflow)
        steps = parsed["jobs"]["mirror"]["steps"]
        return next(s["run"] for s in steps if s.get("id") == "run")

    def test_the_remote_script_is_a_quoted_heredoc(self, workflow: str):
        # Quoted, so the runner's shell substitutes nothing into it.
        assert "<<'REMOTE'" in self.run_step(workflow)

    def test_values_are_passed_as_arguments_not_interpolated(self, workflow: str):
        script = self.run_step(workflow)
        assert 'bash -s --' in script
        assert '--verify "$verify"' in script, "verify must come from $1..$n, not the runner"
        assert "--verify '${VERIFY}'" not in script, "interpolated — injectable"

    def test_verify_is_rejected_unless_numeric(self, workflow: str):
        assert "*[!0-9]*" in self.run_step(workflow)

    def test_the_deploy_path_is_checked_in_the_step_that_uses_it(self, workflow: str):
        # `cd ''` succeeds and lands in $HOME; the guard in an earlier step does
        # not protect this one.
        assert 'DEPLOY_PATH:?' in self.run_step(workflow)


class TestTheSummaryCanActuallyReport:
    """The summary is one of two things a human sees; the other is the tick.

    Its grep anchored `^\\S+` before the level, but `asctime` is
    "2026-08-31 12:08:16,440" — the anchor stops at the date and the level is
    the third field. It matched nothing, on every run. `||` also bound to
    `tail`, which exits 0 on empty input, so the fallback never fired either:
    the block rendered as an empty fence rather than saying anything was wrong.
    """

    REAL_LOG = (
        "2026-08-31 12:08:16,440 INFO preflight ok — source root x resolves\n"
        "2026-08-31 12:08:17,001 INFO listed 4211 object(s)\n"
        "2026-08-31 12:41:02,330 INFO verify: 50 checked, 3 mismatched\n"
        "2026-08-31 12:41:02,331 INFO done — copied 4211, failed 0, "
        "already current 0, skipped 0\n"
    )

    def summary_pattern(self, workflow: str) -> str:
        import re

        match = re.search(r"grep -E '(\^\[0-9[^']+)'", workflow)
        assert match, "no summary grep found in the workflow"
        return match.group(1)

    def test_the_pattern_matches_the_format_the_job_actually_emits(self, workflow: str):
        import subprocess

        pattern = self.summary_pattern(workflow)
        result = subprocess.run(
            ["grep", "-E", pattern], input=self.REAL_LOG,
            capture_output=True, text=True,
        )
        assert result.stdout.strip(), (
            f"pattern {pattern!r} matches nothing in a real log — "
            "the run summary would be empty on every run"
        )
        assert "verify:" in result.stdout, "verification counts missing from the summary"
        assert "done —" in result.stdout, "the closing line missing from the summary"

    def test_the_pattern_expects_the_level_as_the_third_field(self, workflow: str):
        # The specific mistake: anchoring before a timestamp that has a space.
        assert "^\\S+ (INFO" not in workflow

    def test_an_empty_summary_says_so_rather_than_rendering_blank(self, workflow: str):
        assert "no summary produced" in workflow
        assert 'if [ -n "$summary" ]' in workflow, (
            "`| tail || echo` cannot fall back: tail exits 0 on empty input"
        )

    def test_a_failed_verification_is_called_out_in_the_headline(self, workflow: str):
        # Distinct from a plain failure: the copy reported success, so the
        # useful thing to tell someone is that the ledger needs clearing.
        assert "VERIFICATION FAILED" in workflow
        assert "were missing or the wrong size on Box" in workflow


class TestTheHeadlineCarriesTheCounts:
    """A bare "succeeded" cannot be told from a vanished mirror.

    A week that copies nothing looks identical to a week where the Box folder
    had been deleted — both are a green tick. Putting the counts in the
    headline distinguishes them: "nothing new to copy (8,013,796 already on
    Box)" says the mirror is intact; "0 copied, 0 already current" would not.

    The numbers are parsed out of lines the run already prints, so nothing is
    recomputed and nothing extra runs on the deploy host.
    """

    def summary(self, parsed: dict) -> str:
        steps = parsed["jobs"]["mirror"]["steps"]
        return next(
            s["run"] for s in steps
            if s.get("name", "").startswith("Write the run summary")
        )

    def run_summary(self, parsed: dict, log: str, outcome: str = "success") -> str:
        """Execute the real step against a real log, and return the headline."""
        import subprocess
        import tempfile
        from pathlib import Path as P

        script = self.summary(parsed).replace("${{ steps.run.outcome }}", "$OUTCOME")
        with tempfile.TemporaryDirectory() as tmp:
            (P(tmp) / "mirror-output.txt").write_text(log)
            out = P(tmp) / "summary.md"
            subprocess.run(
                ["bash", "-c", script],
                env={
                    "PATH": "/usr/bin:/bin", "RUNNER_TEMP": tmp, "ENV_NAME": "prod",
                    "OUTCOME": outcome, "GITHUB_STEP_SUMMARY": str(out), "LC_ALL": "C",
                },
                capture_output=True, text=True,
            )
            body = out.read_text() if out.exists() else ""
        line = [ln for ln in body.splitlines() if ln.startswith("Result:")]
        return line[0] if line else ""

    DONE = "2026-08-31 02:20:00,1 INFO done — copied {c}, failed 0, already current {a}, skipped 0\n"
    VERIFY = "2026-08-31 02:41:00,1 INFO verify: {n} checked, 0 mismatched\n"

    def test_a_busy_week_names_how_many_were_copied(self, parsed):
        log = self.DONE.format(c=4211, a=0) + self.VERIFY.format(n=50)
        assert "4,211 images copied" in self.run_summary(parsed, log)

    def test_a_quiet_week_says_the_mirror_is_still_there(self, parsed):
        # The case this exists for: nothing copied is only reassuring if the
        # count of what is already on Box is shown beside it.
        log = self.DONE.format(c=0, a=8013796)
        headline = self.run_summary(parsed, log)
        assert "nothing new to copy" in headline
        assert "8,013,796 already on Box" in headline

    def test_the_verification_count_is_shown(self, parsed):
        log = self.DONE.format(c=10, a=0) + self.VERIFY.format(n=50)
        assert "50 verified" in self.run_summary(parsed, log)

    def test_a_log_without_counts_says_so_rather_than_claiming_success(self, parsed):
        headline = self.run_summary(parsed, "ERROR exploded before copying\n")
        assert "no counts in the log" in headline

    def test_a_failed_verification_still_wins_the_headline(self, parsed):
        log = (
            self.DONE.format(c=10, a=0)
            + "2026-08-31 02:41:00,1 ERROR 3 of 50 verified object(s) were "
              "missing or the wrong size on Box.\n"
        )
        assert "VERIFICATION FAILED" in self.run_summary(parsed, log)

    def test_a_stood_down_run_still_wins_the_headline(self, parsed):
        log = (
            "2026-08-31 02:20:00,1 WARNING box-object-backup: SKIPPED — "
            "another run holds the lock\n"
        )
        assert "skipped" in self.run_summary(parsed, log)

    def test_a_failed_run_is_not_reported_as_succeeded(self, parsed):
        headline = self.run_summary(parsed, "ERROR boom\n", outcome="failure")
        assert "FAILED" in headline
        assert "succeeded" not in headline
