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

import contextlib
import os
import subprocess
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

    def test_the_python_actually_prints_the_marker(self, tmp_path, monkeypatch, caplog):
        """Run the stand-down and read the log, rather than grep the source.

        This used to assert `"SKIP_MARKER" in backup_objects.py` — which the
        import line at the top of that file satisfies on its own. Deleting the
        `logger.warning` that actually emits it left this green, which is the
        exact regression the comment here claimed to guard.
        """
        import backup_objects as job
        from runlock import RunLock

        monkeypatch.setattr(job, "run_locked", lambda *a, **kw: 0)
        args = job.parse_args([
            "--env", "prod", "--state-dir", str(tmp_path),
            "--box-root", "Bloom-Backups/BloomV2-Data-Backup/prod/storage",
        ])
        holder = RunLock(tmp_path).acquire()
        try:
            assert job.run_backup(args) == 0
        finally:
            holder.release()
        assert SKIP_MARKER in caplog.text

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
    def test_runs_every_night(self, workflow: str):
        # Nightly, so at most a day's scans exist only in MinIO. Still lands
        # before the Sunday Postgres dump, on the night before it.
        assert 'cron: "17 2 * * *"' in workflow

    def test_does_not_share_the_deploy_concurrency_group(self, workflow: str):
        # A stuck deploy must not cancel the mirror, and vice versa.
        assert "group: box-object-backup-" in workflow
        assert "group: deploy-bloom" not in workflow

    def test_staging_cannot_be_mirrored(self, parsed: dict, workflow: str):
        """Both environments share this host, so they would share the ledger.

        A staging run and the nightly would read and advance each other's
        watermark, each skipping whatever the other's timestamp already
        covered — silently, in both directions. The concurrency group would not
        have stopped it: it was keyed on the environment, so the two ran in
        different groups and GitHub was happy to run them at once.

        YAML 1.1 reads a bare `on:` as the boolean True, which is why the
        trigger block is not `parsed["on"]`.
        """
        inputs = parsed[True]["workflow_dispatch"]["inputs"]
        assert "environment" not in inputs, (
            "the environment input is back; staging can be dispatched again"
        )
        assert "STAGING_DEPLOY_PATH" not in workflow, (
            "the staging deploy path is reachable again"
        )
        assert "staging" not in parsed["jobs"]["mirror"]["name"].lower()

    def test_the_concurrency_group_is_fixed(self, parsed: dict):
        """Keyed on an input, two dispatches did not serialise against each
        other, and neither serialised against the nightly."""
        group = parsed["concurrency"]["group"]
        assert "${{" not in group, f"the group still varies: {group}"

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

    This has now been wrong twice, in two different shells, so these tests run
    the thing rather than reading it.

    First it was interpolated into the ssh command string, where
    `50\'; <command>; echo \'` closed the quote. That was replaced with
    `ssh host bash -s -- "$VERIFY"` and a numeric guard inside the heredoc —
    which looked safe and was not. ssh cannot carry argv boundaries: it joins
    the words after the host into one string for the remote login shell to
    parse, so the values were back in a shell string, and the guard was inside
    `bash -s`, merely the first statement of it. The earlier tests here
    asserted on the shape of the YAML and passed throughout.
    """

    def run_step(self, workflow: str) -> str:
        import yaml

        parsed = yaml.safe_load(workflow)
        steps = parsed["jobs"]["mirror"]["steps"]
        return next(s["run"] for s in steps if s.get("id") == "run")

    def runner_prologue(self, workflow: str) -> str:
        """Everything the runner does before it calls ssh."""
        outer = self.run_step(workflow).split("<<'REMOTE'")[0]
        lines = outer.splitlines()
        stop = next(i for i, ln in enumerate(lines) if ln.strip().startswith("ssh "))
        return "\n".join(lines[:stop])

    def remote_body(self, workflow: str) -> str:
        step = self.run_step(workflow)
        return step.split("<<'REMOTE'", 1)[1].split("\n          REMOTE", 1)[0]

    def build_args(self, workflow: str, **values) -> subprocess.CompletedProcess:
        """Run the real runner-side lines and read back what ssh would send."""
        env = {
            "DEPLOY_PATH": "/srv/bloom", "ENV_NAME": "prod", "VERIFY": "50",
            "RUN_TAG": "1-1", "DRY_RUN": "", "RUNNER_TEMP": "/tmp",
            "PATH": os.environ["PATH"],
        }
        env.update(values)
        return subprocess.run(
            ["bash", "-c", self.runner_prologue(workflow) + '\nprintf "%s" "$remote_args"'],
            capture_output=True, text=True, env=env,
        )

    def send(self, workflow: str, remote_args: str, cwd) -> subprocess.CompletedProcess:
        """What sshd does: join, then hand the one string to a shell."""
        return subprocess.run(
            ["bash", "-c", f"bash -s -- {remote_args}"],
            input=self.remote_body(workflow),
            capture_output=True, text=True, cwd=cwd,
        )

    def test_a_non_numeric_verify_is_refused_before_ssh_is_called(self, workflow: str):
        result = self.build_args(workflow, VERIFY="50; touch PWNED #")
        assert result.returncode != 0, "a payload got past the runner"
        assert "verify must be a whole number" in result.stderr

    def test_verify_still_has_to_be_a_number_at_all(self, workflow: str):
        assert self.build_args(workflow, VERIFY="").returncode != 0
        assert self.build_args(workflow, VERIFY="all").returncode != 0
        assert self.build_args(workflow, VERIFY="50").returncode == 0

    def test_shell_syntax_in_a_value_does_not_execute_on_the_remote(
        self, workflow: str, tmp_path
    ):
        """The layer under the numeric guard.

        `verify` is checked, but the other values are not — they are secrets
        and a resolved env name, and nothing validates their characters. If the
        quoting is what stands between a value and the remote shell, then a
        value full of shell syntax must arrive as text and nothing else.
        """
        payload = f"/srv/bloom; touch {tmp_path}/PWNED #"
        built = self.build_args(workflow, DEPLOY_PATH=payload)
        assert built.returncode == 0, built.stderr

        self.send(workflow, built.stdout, tmp_path)
        assert not (tmp_path / "PWNED").exists(), (
            "the remote shell executed part of a value — ssh joined the "
            "arguments and nothing quoted them"
        )

    def test_the_value_still_arrives_intact(self, workflow: str, tmp_path):
        """Quoting that mangles the value is not a fix either."""
        odd = str(tmp_path / "a dir with spaces")
        built = self.build_args(workflow, DEPLOY_PATH=odd)
        assert built.returncode == 0, built.stderr
        result = self.send(workflow, built.stdout, tmp_path)
        # cd fails on a directory that does not exist, and names what it tried.
        assert odd in (result.stderr + result.stdout), (
            f"the path did not survive the round trip: {result.stderr}"
        )

    def test_without_the_quoting_the_payload_would_run(self, workflow: str, tmp_path):
        """A control, so the reason for the quoting cannot be misread.

        Not a test of our code — a demonstration of the mechanism the previous
        fix assumed did not exist. If this ever stops creating the file, ssh
        has changed and the comment in the workflow needs revisiting.
        """
        payload = f"/srv/bloom; touch {tmp_path}/PWNED #"
        unquoted = f"{payload} prod 50 1-1 "
        self.send(workflow, unquoted, tmp_path)
        assert (tmp_path / "PWNED").exists(), (
            "joining unquoted arguments no longer executes them"
        )

    def test_the_remote_script_is_a_quoted_heredoc(self, workflow: str):
        # Quoted, so the runner's shell substitutes nothing into it. Separate
        # from the argument quoting above, and not a substitute for it.
        assert "<<'REMOTE'" in self.run_step(workflow)

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


class TestCancellingTheJobStopsTheRun:
    """Cancelling kills the ssh client on the runner, not the run on the host.

    The remote sees the connection drop, which arrives as SIGHUP. That used to
    be a hard kill — the cleanup in `finally` never ran and the rclone
    container was left holding the RC port, so the next run refused to start.
    """

    def step(self, parsed: dict) -> dict:
        steps = parsed["jobs"]["mirror"]["steps"]
        matches = [
            s for s in steps if s.get("name", "").startswith("Ask the host to stop")
        ]
        assert matches, "no cancellation step — cancelling would leave the run going"
        return matches[0]

    def test_it_only_runs_when_the_job_was_cancelled(self, parsed: dict):
        assert self.step(parsed)["if"] == "cancelled()"

    def test_it_runs_before_the_summary(self, parsed: dict):
        # So the summary describes a run that has actually stopped.
        names = [s.get("name", "") for s in parsed["jobs"]["mirror"]["steps"]]
        stop = next(i for i, n in enumerate(names) if n.startswith("Ask the host to stop"))
        summary = next(i for i, n in enumerate(names) if n.startswith("Write the run summary"))
        assert stop < summary

    def test_it_finds_the_process_through_the_lock_file(self, parsed: dict):
        # runlock.py writes the pid there; nothing else knows what is running.
        script = self.step(parsed)["run"]
        assert "backup.lock" in script
        assert '"pid"' in script

    def test_it_asks_rather_than_kills(self, parsed: dict):
        # SIGKILL is the hard kill this whole change exists to avoid: it would
        # leave the container behind exactly as before.
        # Comments stripped: the script's own comment explains why it does NOT
        # escalate to SIGKILL, and a substring check on the raw text trips over
        # that explanation rather than on any code.
        script = _strip_comments(self.step(parsed)["run"])
        assert "kill -TERM" in script
        # `kill -9` specifically: a bare "-9" also appears inside the [!0-9]
        # character class that validates the pid.
        assert "kill -9" not in script
        assert "-KILL" not in script
        assert "SIGKILL" not in script

    def test_it_cannot_hang_the_job(self, parsed: dict):
        step = self.step(parsed)
        assert step.get("timeout-minutes"), "no timeout on a step that waits"
        assert "ConnectTimeout" in step["run"]

    def test_the_shell_parses(self, parsed: dict):
        import subprocess
        import tempfile
        from pathlib import Path as P

        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as handle:
            handle.write(self.step(parsed)["run"])
            path = handle.name
        result = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
        P(path).unlink()
        assert result.returncode == 0, result.stderr


class TestTheRunStepStampsItsMarker:
    """The other half of the ownership guard.

    The cancel step refuses to signal anything unless the marker names this
    job. If the run step stops writing it, the cancel step stays silent and a
    cancelled run keeps going on the host — the leak the whole step exists to
    close, and nothing else in the suite would notice.
    """

    def remote_script(self, parsed: dict) -> str:
        steps = parsed["jobs"]["mirror"]["steps"]
        outer = next(
            s for s in steps if s.get("name", "").startswith("Run the mirror")
        )["run"]
        return outer.split("<<'REMOTE'", 1)[1].split("\n          REMOTE", 1)[0]

    def test_the_marker_is_written_before_the_job_is_launched(self, parsed: dict):
        script = self.remote_script(parsed)
        marker = "/var/lib/bloom-box-object-backup/actions-run.started"
        assert marker in script, "the run step never stamps the marker"
        launch = script.index("backup_objects.py")
        assert script.index(marker) < launch, (
            "stamped after the job starts — the window where it is missing is "
            "exactly when a cancellation is most likely"
        )

    def test_the_marker_names_this_job_and_the_time(self, parsed: dict, tmp_path):
        """Run the stamping line for real and read back what it wrote.

        The tag alone lets a marker from an earlier job pass; the time alone
        lets this job's own stand-down pass. Both, or the guard has a hole.
        """
        import subprocess

        script = self.remote_script(parsed)
        marker = tmp_path / "actions-run.started"
        lines = [ln.strip() for ln in script.splitlines()]
        start = next(i for i, ln in enumerate(lines) if ln.startswith("marker="))
        write = next(i for i, ln in enumerate(lines) if '> "$marker"' in ln)
        block = "\n".join(lines[start:write + 1]).replace(
            "/var/lib/bloom-box-object-backup/actions-run.started", str(marker)
        )
        subprocess.run(
            ["bash", "-c", f'set -e\nrun_tag="$1"\n{block}', "bash", "42-7"],
            check=True, capture_output=True, text=True,
        )
        tag, stamped = marker.read_text().split()
        assert tag == "42-7", f"the marker does not name the job: {tag}"
        assert int(stamped) > 1_700_000_000, f"not a plausible timestamp: {stamped}"


class TestTheStopScriptBehaves:
    """Run the remote half against real lock files, with a fake ssh."""

    def remote_script(self, parsed: dict) -> str:
        steps = parsed["jobs"]["mirror"]["steps"]
        outer = next(
            s for s in steps if s.get("name", "").startswith("Ask the host to stop")
        )["run"]
        # The remote half is the quoted heredoc body.
        return outer.split("<<'REMOTE'", 1)[1].split("REMOTE", 1)[0].split("\n", 1)[1]

    RUN_TAG = "1234567890-1"

    def run_remote(self, parsed: dict, lock_dir, contents=None, marker="same-job"):
        """Run the remote half, with the lock and the marker under `lock_dir`.

        `marker` says what this job left behind on the host:
          "same-job"  this job started the run holding the lock (the norm)
          "other-job" a marker from an earlier job — a stale file
          "stood-down" this job's marker, but the lock predates it, which is
                       what a nightly that found the seed's lock leaves
          None        no marker at all
        """
        import subprocess

        script = self.remote_script(parsed).replace(
            "/var/lib/bloom-box-object-backup/backup.lock", f"{lock_dir}/backup.lock"
        ).replace(
            "/var/lib/bloom-box-object-backup/actions-run.started",
            f"{lock_dir}/actions-run.started",
        )
        if contents is not None:
            (lock_dir / "backup.lock").write_text(contents)
        if marker is not None:
            tag = self.RUN_TAG if marker != "other-job" else "999-1"
            # The stood-down case stamps the marker AFTER the lock was taken.
            stamped = 2_000_000_000 if marker == "stood-down" else 1
            (lock_dir / "actions-run.started").write_text(f"{tag} {stamped}\n")
        return subprocess.run(
            ["bash", "-c", script, "bash", self.RUN_TAG],
            capture_output=True, text=True,
        )

    def test_no_lock_file_is_not_an_error(self, parsed: dict, tmp_path):
        result = self.run_remote(parsed, tmp_path)
        assert result.returncode == 0
        assert "nothing was running" in result.stdout

    def test_a_lock_without_a_pid_is_not_an_error(self, parsed: dict, tmp_path):
        result = self.run_remote(parsed, tmp_path, contents='{"started_at": 1700000000}')
        assert result.returncode == 0
        assert "nothing to stop" in result.stdout

    def test_a_stale_pid_is_not_an_error(self, parsed: dict, tmp_path):
        # The kernel drops the flock when the holder dies, but the metadata can
        # outlive it.
        result = self.run_remote(parsed, tmp_path, contents='{"pid": 999999, "started_at": 1700000000}')
        assert result.returncode == 0
        assert "gone already" in result.stdout

    def test_garbage_in_the_lock_file_is_not_an_error(self, parsed: dict, tmp_path):
        result = self.run_remote(parsed, tmp_path, contents="not json at all")
        assert result.returncode == 0

    @contextlib.contextmanager
    def live_child(self):
        """A process that exits 3 on SIGTERM, so being signalled is visible.

        Reaped in a thread throughout. `kill -0` succeeds on a zombie, so an
        unreaped child makes the script wait out its whole minute before
        reporting — which turns a guard that fails to spare the process into a
        one-minute test instead of an immediate one.
        """
        import subprocess
        import sys
        import threading

        child = subprocess.Popen(
            [sys.executable, "-c",
             "import signal,sys,time\n"
             "signal.signal(signal.SIGTERM, lambda *a: sys.exit(3))\n"
             "print('up', flush=True)\n"
             "time.sleep(60)"],
            stdout=subprocess.PIPE, text=True,
        )
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "up"
        reaper = threading.Thread(target=child.wait, daemon=True)
        reaper.start()
        try:
            yield child
        finally:
            child.kill()
            reaper.join(timeout=10)

    def test_a_marker_from_an_earlier_job_spares_the_run(self, parsed: dict, tmp_path):
        """A marker is left on the host by every job that starts a run.

        Reading one from last night and stopping whatever holds the lock today
        is the same mistake as having no guard at all, so the tag has to match
        this job before anything is signalled.
        """
        import json

        with self.live_child() as child:
            result = self.run_remote(
                parsed, tmp_path,
                contents=json.dumps({"pid": child.pid, "started_at": 1_700_000_000}),
                marker="other-job",
            )
            assert "another job" in result.stdout, result.stdout
            assert child.poll() is None, "signalled a run this job never started"

    def test_the_seed_is_spared_when_a_stood_down_job_is_cancelled(
        self, parsed: dict, tmp_path
    ):
        """The case this guard exists for.

        The seed holds the lock for days. A nightly starts, stamps its own
        marker, finds the lock held and stands down — leaving the seed's pid in
        the lock file and its own tag in the marker. Cancelling that stood-down
        job must not stop the seed: weeks of copying, halted silently, with the
        Actions run reporting only that it was cancelled.

        The tag matches here, so only the start times tell the two apart.
        """
        import json

        with self.live_child() as seed:
            result = self.run_remote(
                parsed, tmp_path,
                contents=json.dumps({"pid": seed.pid, "started_at": 1_700_000_000}),
                marker="stood-down",
            )
            assert "already running before this job" in result.stdout, result.stdout
            assert seed.poll() is None, "stopped the seed while cancelling another job"

    def test_no_marker_at_all_spares_the_run(self, parsed: dict, tmp_path):
        import json

        with self.live_child() as child:
            result = self.run_remote(
                parsed, tmp_path,
                contents=json.dumps({"pid": child.pid, "started_at": 1_700_000_000}),
                marker=None,
            )
            assert "no marker" in result.stdout, result.stdout
            assert child.poll() is None, "signalled without knowing whose run it is"

    def test_a_lock_with_no_start_time_spares_the_run(self, parsed: dict, tmp_path):
        """Nothing writes such a lock today, but guessing is the wrong default."""
        import json

        with self.live_child() as child:
            result = self.run_remote(
                parsed, tmp_path, contents=json.dumps({"pid": child.pid}),
            )
            assert "cannot compare" in result.stdout, result.stdout
            assert child.poll() is None

    def test_a_live_process_is_asked_to_stop(self, parsed: dict, tmp_path):
        """A running process is signalled, exits on its own, and is seen to.

        The child is reaped in a thread while the script polls. Without that it
        lingers as a zombie, and `kill -0` succeeds on a zombie — so the script
        would wait out its full timeout against a process that had already
        exited. Real runs do not hit this: a seed in tmux is reaped by tmux,
        and a workflow run is orphaned to init when the ssh shell exits.
        """
        import json
        import subprocess
        import sys
        import threading

        child = subprocess.Popen(
            [sys.executable, "-c",
             "import signal,sys,time\n"
             "signal.signal(signal.SIGTERM, lambda *a: sys.exit(3))\n"
             "print('up', flush=True)\n"
             "time.sleep(60)"],
            stdout=subprocess.PIPE, text=True,
        )
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "up"

        status = {}
        reaper = threading.Thread(target=lambda: status.setdefault("rc", child.wait()))
        reaper.start()
        try:
            result = self.run_remote(
                parsed, tmp_path, contents=json.dumps({"pid": child.pid, "started_at": 1_700_000_000})
            )
            assert "asking pid" in result.stdout, result.stdout
            reaper.join(timeout=15)
            assert status.get("rc") == 3, "it was killed rather than asked to stop"
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
