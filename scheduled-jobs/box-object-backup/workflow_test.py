"""Tests for the cross-file contract between this job and its workflow.

The job prints `BOX_BACKUP_STATUS=` and `BOX_BACKUP_FLAGS=` and the YAML reads
them, anchored to the start of a log line. That is a contract spanning two
files in two languages, over a closed vocabulary defined in only one of them —
exactly the kind that rots silently. An earlier version of this job carried a
comment naming a workflow file that did not exist at all, and nothing noticed.

Some assertions here are about *shape*: they cannot prove the workflow runs,
only that it still agrees with the code it drives. The ones that matter most
execute the real step under `bash -e` against a real log instead, because the
order of a branch chain is not visible in any single line of it.
"""

from __future__ import annotations

import contextlib
import io
import logging
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import backup_objects as job
import summary
from runlock import SKIP_MARKER

WORKFLOW = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "box-object-backup.yml"
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
    return next(
        s["run"] for s in steps if s.get("name", "").startswith("Write the run summary")
    )


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


class TestTheJobAndTheRendererAgree:
    """The one contract that spans the two files.

    The job prints a verdict; `summary.py` reads it. Both vocabularies are
    closed, in different files, and a value in one and not the other is a
    branch that can never fire. What each night reads as is tested in
    summary_test.py, against the renderer itself.
    """

    def test_the_renderers_anchor_matches_the_format_the_job_logs(self):
        """The anchor is timestamp, level, key — and `asctime` has a space in
        it, so anchoring one field too early matches nothing on every run.

        An earlier pattern anchored `^\\S+` before the level and silently
        produced an empty page for every night.
        """
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter(job.LOG_FORMAT))
        logger = logging.getLogger("anchor-check")
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False
        with contextlib.redirect_stdout(io.StringIO()):
            monkey = job.logger
            job.logger = logger
            try:
                job.emit_status("partial", ["collisions"], {"copied": 7})
            finally:
                job.logger = monkey
        found = summary.from_log(stream.getvalue())
        assert found is not None, (
            "the renderer's anchor does not match the job's log format — "
            "the summary would be empty on every run"
        )
        assert found.status == "partial"
        assert found.flags == ("collisions",)
        assert found.count("copied") == 7

    def test_the_progress_lines_the_renderer_quotes_are_ones_the_job_prints(self):
        real = (
            "2026-08-31 12:08:16,440 INFO preflight ok — source root resolves\n"
            "2026-08-31 12:08:17,001 INFO listed 4211 object(s)\n"
            "2026-08-31 12:41:02,330 INFO verify: 50 checked, 3 mismatched\n"
            "2026-08-31 12:41:02,331 INFO done — copied 4211, failed 0, "
            "already current 0, skipped 0\n"
        )
        quoted = summary.tail(real)
        assert len(quoted) == 4, f"the renderer quotes none of a real log: {quoted}"

    def test_the_step_only_looks_for_a_verdict_it_cannot_be_fooled_by(
        self, summary_script: str
    ):
        # The step's own pre-check decides whether to fall back to the host.
        # Unanchored, a refused object name containing the key would make a
        # lost night look like one that reported for itself.
        assert "'^[0-9-]+ [0-9:,]+ [A-Z]+ BOX_BACKUP_STATUS='" in summary_script, (
            "the step's verdict check is not anchored to the start of a line"
        )

    def test_the_job_emits_a_status_the_summary_knows(
        self, tmp_path, monkeypatch, caplog
    ):
        """Both halves of the contract, in one place.

        The vocabulary is closed on the Python side and branched on in the
        YAML; a value in one and not the other is a branch that never fires.
        """
        import logging as _logging

        caplog.set_level(_logging.INFO)
        job.emit_status("skipped")
        assert f"{job.STATUS_KEY}=skipped" in caplog.text
        with pytest.raises(ValueError):
            job.emit_status("no-such-status")
        with pytest.raises(ValueError):
            job.emit_status("ok", ["no-such-flag"])

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
        args = job.parse_args(
            [
                "--env",
                "prod",
                "--state-dir",
                str(tmp_path),
                "--box-root",
                "Bloom-Backups/BloomV2-Data-Backup/prod/storage",
            ]
        )
        holder = RunLock(tmp_path).acquire()
        try:
            assert job.run_backup(args) == 0
        finally:
            holder.release()
        assert SKIP_MARKER in caplog.text


class TestScheduleShape:
    def test_runs_every_night(self, parsed: dict):
        # Nightly, so at most a day's scans exist only in MinIO. Still lands
        # before the Sunday Postgres dump, on the night before it.
        #
        # Read from the parsed schedule, not grepped: a comment quoting the
        # cron line satisfied the raw search whatever the real trigger said.
        assert [s["cron"] for s in parsed[True]["schedule"]] == ["17 2 * * *"]

    def test_does_not_share_the_deploy_concurrency_group(self, parsed: dict):
        # A stuck deploy must not cancel the mirror, and vice versa. Parsed,
        # for the same reason: this is the exact failure the test names, and
        # a commented-out group would have satisfied a substring search.
        group = parsed["concurrency"]["group"]
        assert group.startswith("box-object-backup-"), group
        assert "deploy-bloom" not in group

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
    removed the only mechanism feeding OBJECT_BACKUP_*, POSTGRES_* and
    MINIO_ROOT_* to
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
        assert ".env.$env_name" in script or ".env.$ENV_NAME" in script, (
            "nothing reads the deploy env file, so the job runs with no config"
        )

    def test_every_variable_family_the_job_needs_is_exported(self, workflow: str):
        script = self.run_step(workflow)
        for family in ("OBJECT_BACKUP_", "POSTGRES_", "MINIO_ROOT_"):
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
        assert "OBJECT_BACKUP_" in families and "MINIO_ROOT_" in families
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
            "DEPLOY_PATH": "/srv/bloom",
            "ENV_NAME": "prod",
            "VERIFY": "50",
            "RUN_TAG": "1-1",
            "DRY_RUN": "",
            "RUNNER_TEMP": "/tmp",
            "STATE_DIR": "/var/lib/bloom-box-object-backup",
            "PATH": os.environ["PATH"],
        }
        env.update(values)
        return subprocess.run(
            [
                "bash",
                "-c",
                self.runner_prologue(workflow) + '\nprintf "%s" "$remote_args"',
            ],
            capture_output=True,
            text=True,
            env=env,
        )

    def send(self, workflow: str, remote_args: str, cwd) -> subprocess.CompletedProcess:
        """What sshd does: join, then hand the one string to a shell."""
        return subprocess.run(
            ["bash", "-c", f"bash -s -- {remote_args}"],
            input=self.remote_body(workflow),
            capture_output=True,
            text=True,
            cwd=cwd,
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
        assert "DEPLOY_PATH:?" in self.run_step(workflow)


class TestTheWorkflowAndTheJobWatchOneDirectory:
    """Three ssh sessions, and only one of them reads the env file.

    The run stamps its marker in the state directory, the cancel step finds
    the lock there, and the summary falls back to the reports there. Pointed
    somewhere else, the job would work perfectly while those two watched an
    empty directory: a cancel that stops nothing, and a verdict never
    recovered — both silent.
    """

    def test_the_directory_is_defined_once(self, parsed: dict, workflow: str):
        assert parsed["env"]["STATE_DIR"] == job.DEFAULT_STATE_DIR, (
            "the workflow and the job disagree about where the state lives"
        )
        # Every other mention must go through it.
        body = workflow.split("env:", 1)[1]
        assert body.count(job.DEFAULT_STATE_DIR) == 1, (
            "the path is still written out somewhere a change would miss"
        )

    def test_a_job_pointed_elsewhere_refuses_to_start(self, parsed: dict, tmp_path):
        """Loud on the run step rather than silent in the other two."""
        import subprocess

        steps = parsed["jobs"]["mirror"]["steps"]
        outer = next(
            s for s in steps if s.get("name", "").startswith("Run the mirror")
        )["run"]
        remote = outer.split("<<'REMOTE'", 1)[1].split("\n          REMOTE", 1)[0]
        guard_at = remote.index("OBJECT_BACKUP_STATE_DIR is")
        block = remote[: guard_at + remote[guard_at:].index("fi") + 2]
        block = block[block.index('if [ -n "${OBJECT_BACKUP_STATE_DIR:-}"') :]
        result = subprocess.run(
            [
                "bash",
                "-c",
                f'state_dir="$1"\n{block}\necho reached-the-run',
                "bash",
                "/var/lib/bloom-box-object-backup",
            ],
            env={"OBJECT_BACKUP_STATE_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2, result.stdout
        assert "reached-the-run" not in result.stdout
        assert "Point them at the same directory" in result.stderr

    def test_the_matching_case_is_allowed_through(self, parsed: dict):
        """The other half: the guard must not stop the ordinary night."""
        import subprocess

        steps = parsed["jobs"]["mirror"]["steps"]
        outer = next(
            s for s in steps if s.get("name", "").startswith("Run the mirror")
        )["run"]
        remote = outer.split("<<'REMOTE'", 1)[1].split("\n          REMOTE", 1)[0]
        guard_at = remote.index("OBJECT_BACKUP_STATE_DIR is")
        block = remote[: guard_at + remote[guard_at:].index("fi") + 2]
        block = block[block.index('if [ -n "${OBJECT_BACKUP_STATE_DIR:-}"') :]
        for env in (
            {},
            {"OBJECT_BACKUP_STATE_DIR": "/var/lib/bloom-box-object-backup"},
        ):
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    f'state_dir="$1"\n{block}\necho reached-the-run',
                    "bash",
                    "/var/lib/bloom-box-object-backup",
                ],
                env={"PATH": "/usr/bin:/bin", **env},
                capture_output=True,
                text=True,
            )
            assert "reached-the-run" in result.stdout, (env, result.stderr)


class TestTheSummaryStepCannotHangTheRunner:
    """It fires exactly when the host may be wedged, on a shared runner."""

    def step(self, parsed: dict) -> dict:
        steps = parsed["jobs"]["mirror"]["steps"]
        return next(
            s for s in steps if s.get("name", "").startswith("Write the run summary")
        )

    def test_it_is_bounded(self, parsed: dict):
        assert self.step(parsed).get("timeout-minutes") == 5

    def test_every_ssh_it_makes_gives_up_on_an_unreachable_host(self, parsed: dict):
        """A default connect can sit for two minutes, three times over."""
        body = self.step(parsed)["run"]
        calls = body.count("ssh -i ~/.ssh/deploy_key")
        assert calls == 1, (
            f"{calls} ssh invocations — they share one helper, so a second "
            "one means an option was duplicated or omitted somewhere"
        )
        assert body.count("-o ConnectTimeout=") == calls, (
            "the ssh call in the summary step has no connect timeout"
        )
        assert body.count("-o BatchMode=yes") == calls, (
            "without BatchMode ssh can sit at a password prompt"
        )


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
        stop = next(
            i for i, n in enumerate(names) if n.startswith("Ask the host to stop")
        )
        summary = next(
            i for i, n in enumerate(names) if n.startswith("Write the run summary")
        )
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
        marker = '"$state_dir/actions-run.started"'
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
        block = "\n".join(lines[start : write + 1])
        subprocess.run(
            [
                "bash",
                "-c",
                f'set -e\nrun_tag="$1"\nstate_dir="$2"\n{block}',
                "bash",
                "42-7",
                str(tmp_path),
            ],
            check=True,
            capture_output=True,
            text=True,
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

        # The state directory is an argument now, so the harness supplies a
        # temporary one rather than rewriting paths out of the script — which
        # means what runs here is the script as written, character for
        # character.
        script = self.remote_script(parsed)
        if contents is not None:
            (lock_dir / "backup.lock").write_text(contents)
        if marker is not None:
            tag = self.RUN_TAG if marker != "other-job" else "999-1"
            # The stood-down case stamps the marker AFTER the lock was taken.
            stamped = 2_000_000_000 if marker == "stood-down" else 1
            (lock_dir / "actions-run.started").write_text(f"{tag} {stamped}\n")
        return subprocess.run(
            ["bash", "-c", script, "bash", self.RUN_TAG, str(lock_dir)],
            capture_output=True,
            text=True,
        )

    def test_no_lock_file_is_not_an_error(self, parsed: dict, tmp_path):
        result = self.run_remote(parsed, tmp_path)
        assert result.returncode == 0
        assert "nothing was running" in result.stdout

    def test_a_lock_without_a_pid_is_not_an_error(self, parsed: dict, tmp_path):
        result = self.run_remote(
            parsed, tmp_path, contents='{"started_at": 1700000000}'
        )
        assert result.returncode == 0
        assert "nothing to stop" in result.stdout

    def test_a_stale_pid_is_not_an_error(self, parsed: dict, tmp_path):
        # The kernel drops the flock when the holder dies, but the metadata can
        # outlive it.
        result = self.run_remote(
            parsed, tmp_path, contents='{"pid": 999999, "started_at": 1700000000}'
        )
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
            [
                sys.executable,
                "-c",
                "import signal,sys,time\n"
                "signal.signal(signal.SIGTERM, lambda *a: sys.exit(3))\n"
                "print('up', flush=True)\n"
                "time.sleep(60)",
            ],
            stdout=subprocess.PIPE,
            text=True,
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
                parsed,
                tmp_path,
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
                parsed,
                tmp_path,
                contents=json.dumps({"pid": seed.pid, "started_at": 1_700_000_000}),
                marker="stood-down",
            )
            assert "already running before this job" in result.stdout, result.stdout
            assert seed.poll() is None, "stopped the seed while cancelling another job"

    def test_no_marker_at_all_spares_the_run(self, parsed: dict, tmp_path):
        import json

        with self.live_child() as child:
            result = self.run_remote(
                parsed,
                tmp_path,
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
                parsed,
                tmp_path,
                contents=json.dumps({"pid": child.pid}),
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
            [
                sys.executable,
                "-c",
                "import signal,sys,time\n"
                "signal.signal(signal.SIGTERM, lambda *a: sys.exit(3))\n"
                "print('up', flush=True)\n"
                "time.sleep(60)",
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "up"

        status = {}
        reaper = threading.Thread(target=lambda: status.setdefault("rc", child.wait()))
        reaper.start()
        try:
            result = self.run_remote(
                parsed,
                tmp_path,
                contents=json.dumps({"pid": child.pid, "started_at": 1_700_000_000}),
            )
            assert "asking pid" in result.stdout, result.stdout
            reaper.join(timeout=15)
            assert status.get("rc") == 3, "it was killed rather than asked to stop"
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)


class TestGitHubCanActuallyLoadThisWorkflow:
    """A workflow GitHub cannot parse fails silently and notifies nobody.

    The summary step's script grew past GitHub's 21,000-character limit for a
    single expression. Every push then produced a zero-job run reading
    "Invalid workflow file … Exceeded max expression length 21000", the
    Actions API listed the workflow's name as its own file path, and — the
    part that matters — the 02:17 schedule would never have produced a run at
    all. Nothing in the suite measured it, and YAML validity does not catch
    it: the file parses perfectly, GitHub just refuses it.
    """

    # GitHub's hard cap. The assertion uses a margin rather than the cap
    # itself, because landing at 20,999 means the next comment breaks
    # production and this test says nothing until it is too late.
    MAX_EXPRESSION = 21_000
    MARGIN = 1_000

    def test_no_step_script_approaches_the_expression_limit(self, parsed: dict):
        oversized = []
        for job_name, job_body in parsed["jobs"].items():
            for step in job_body.get("steps", []):
                script = step.get("run")
                if script and len(script) > self.MAX_EXPRESSION - self.MARGIN:
                    oversized.append(
                        f"{job_name}/{step.get('name')}: {len(script)} chars"
                    )
        assert not oversized, (
            "GitHub refuses a run: block over "
            f"{self.MAX_EXPRESSION} characters and the whole workflow then "
            "fails to load — no schedule, no dispatch, no notification. "
            f"Over the {self.MAX_EXPRESSION - self.MARGIN} warning line: "
            + "; ".join(oversized)
        )


class TestTheSummaryStepFeedsTheRenderer:
    """What the step still does itself: find the verdict and hand it over.

    The wording is summary.py's and is tested there. What is shell here is the
    choice between the log and the host's report, and the search that finds
    that report — so these run the real step, and only assert on that choice.
    """

    REPO = Path(__file__).resolve().parents[2]

    # Nights whose logs say nothing final: the pipe died, or there was nothing
    # to say. Each one used to be a red tick and a blank page under errexit.
    NIGHTS = {
        "stood down": "2026-08-31 02:20:00,1 WARNING box-object-backup: SKIPPED\n",
        "preflight died": "2026-08-31 02:20:00,1 ERROR preflight: no container\n",
        "truncated": "2026-08-31 02:20:00,1 INFO batch: 20000 object(s)\n",
        "empty log": "",
        "clean": (
            "2026-08-31 02:41:00,1 INFO BOX_BACKUP_STATUS=ok\n"
            "2026-08-31 02:41:00,1 INFO BOX_BACKUP_FLAGS=\n"
            '2026-08-31 02:41:00,1 INFO BOX_BACKUP_STATS={"copied": 4211}\n'
        ),
    }

    def run_step(self, script, log, tmp, argv=("bash", "-e", "-c"), **env_over):
        """Execute the step as GitHub would, and return (rc, page, stderr)."""
        root = Path(tmp)
        root.mkdir(parents=True, exist_ok=True)
        (root / "mirror-output.txt").write_text(log)
        state = root / "state"
        (state / "_runs").mkdir(parents=True, exist_ok=True)
        out = root / "summary.md"
        env = {
            "PATH": f"{root / 'bin'}:{Path(sys.executable).parent}:/usr/bin:/bin",
            "RUNNER_TEMP": str(root),
            "ENV_NAME": "prod",
            "STATE_DIR": str(state),
            "RUN_TAG": "1-1",
            "GITHUB_STEP_SUMMARY": str(out),
            "LC_ALL": "C",
        }
        env.update(env_over)
        result = subprocess.run(
            [*argv, script.replace("${{ steps.run.outcome }}", "$OUTCOME")],
            env={**env, "OUTCOME": env.get("OUTCOME", "success")},
            cwd=self.REPO,
            capture_output=True,
            text=True,
        )
        return (
            result.returncode,
            (out.read_text() if out.exists() else ""),
            result.stderr,
        )

    def fake(self, tmp, name, body):
        path = Path(tmp) / "bin" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)

    @pytest.mark.parametrize("night", sorted(NIGHTS))
    def test_the_step_behaves_the_same_with_and_without_errexit(
        self, summary_script, night, tmp_path
    ):
        """GitHub runs a `run:` block as `bash -e {0}`.

        Asserting "the harness passes -e" would only test the harness. Running
        it both ways and requiring agreement tests the property that matters:
        nothing in the step may depend on errexit being off. A version of this
        step that did took seven review rounds to find, because the harness ran
        plain `bash` and every night of a seed would have been a red tick over
        an empty page.
        """
        log = self.NIGHTS[night]
        plain = self.run_step(summary_script, log, tmp_path / "a", argv=("bash", "-c"))
        errexit = self.run_step(summary_script, log, tmp_path / "b")
        assert errexit[0] == plain[0] == 0, (
            f"{night}: exited {errexit[0]}: {errexit[2][:200]}"
        )
        assert errexit[1] == plain[1], f"{night}: errexit changes what GitHub renders"
        assert errexit[1].strip(), f"{night}: the step wrote nothing"

    def test_a_verdict_in_the_log_is_used_without_touching_the_host(
        self, summary_script, tmp_path
    ):
        self.fake(tmp_path, "ssh", "echo REACHED-THE-HOST >&2; exit 1\n")
        rc, page, err = self.run_step(
            summary_script,
            self.NIGHTS["clean"],
            tmp_path,
            DEPLOY_USER="deploy",
            DEPLOY_HOST="host",
        )
        assert rc == 0 and "4,211 images copied" in page
        assert "REACHED-THE-HOST" not in err

    def test_a_lost_verdict_is_recovered_from_the_report_on_the_host(
        self, summary_script, tmp_path
    ):
        report = {"status": "stopped", "flags": [], "stats": {"copied": 400000}}
        self.fake(tmp_path, "ssh", f"cat <<'JSON'\n{json.dumps(report)}\nJSON\n")
        rc, page, _ = self.run_step(
            summary_script,
            self.NIGHTS["truncated"],
            tmp_path,
            OUTCOME="failure",
            DEPLOY_USER="deploy",
            DEPLOY_HOST="host",
        )
        assert rc == 0, page
        assert "stopped, progress kept" in page
        assert "It got through 400,000 images copied." in page

    def test_the_fallback_is_skipped_when_the_deploy_secrets_are_absent(
        self, summary_script, tmp_path
    ):
        # A dispatch from a fork has no secrets; the step must still render.
        self.fake(tmp_path, "ssh", "echo REACHED-THE-HOST >&2; exit 1\n")
        rc, page, err = self.run_step(
            summary_script, self.NIGHTS["truncated"], tmp_path, OUTCOME="failure"
        )
        assert rc == 0 and page.strip()
        assert "REACHED-THE-HOST" not in err

    def search_args(self, summary_script, marker, tmp_path):
        """The args the report search was built with, or None if it never ran.

        Observed rather than executed: `-printf` and `-newermt` are GNU-only,
        so on a BSD box the real command returns nothing whatever the flags
        say and an execution test cannot tell a mutation apart. What the step
        BUILDS is the property that matters, and it holds anywhere.
        """
        state = tmp_path / "state"
        (state / "_runs").mkdir(parents=True, exist_ok=True)
        (state / "actions-run.started").write_text(marker + "\n")
        seen = tmp_path / "find-args.txt"
        # ssh hands its trailing words to a shell with the heredoc on stdin.
        self.fake(
            tmp_path,
            "ssh",
            'while [ $# -gt 0 ]; do case "$1" in *@*) shift; break ;; *) shift ;; esac; done\n'
            'exec /bin/sh -c "$*"\n',
        )
        self.fake(tmp_path, "find", f'printf "%s\\n" "$*" > {seen}\n')
        rc, _, err = self.run_step(
            summary_script,
            self.NIGHTS["truncated"],
            tmp_path,
            OUTCOME="failure",
            DEPLOY_USER="deploy",
            DEPLOY_HOST="host",
        )
        assert rc == 0, err[:300]
        return (seen.read_text() if seen.exists() else None), str(state)

    def test_the_search_is_scoped_to_reports_written_after_this_run_began(
        self, summary_script, tmp_path
    ):
        """The seed writes into the same `_runs/` directory.

        The marker names this job, so the tag check passes — but without
        `-newermt` the newest report there is the seed's, and the seed's
        verdict becomes this night's headline.
        """
        args, state = self.search_args(summary_script, "1-1 1700000000", tmp_path)
        assert args is not None, "the fallback never searched for a report"
        assert "-newermt @1700000000" in args, f"not scoped to this run: {args}"
        assert f"{state}/_runs" in args

    def test_the_search_does_not_run_when_the_marker_names_another_job(
        self, summary_script, tmp_path
    ):
        args, _ = self.search_args(summary_script, "999-1 1700000000", tmp_path)
        assert args is None, "it searched for a report belonging to another job"

    @pytest.mark.parametrize("marker", ["1-1 garbage", "1-1", "1-1 17e9", "1-1 -5"])
    def test_a_marker_without_a_usable_time_searches_for_nothing(
        self, summary_script, marker, tmp_path
    ):
        # Without the numeric check the step builds `-newermt @<garbage>`;
        # without the emptiness check, a bare `-newermt @`. Either loses the
        # scoping, which is the whole point of the flag.
        args, _ = self.search_args(summary_script, marker, tmp_path)
        assert args is None, f"marker {marker!r} produced a search anyway: {args}"

    def test_the_renderer_it_calls_exists(self, summary_script):
        called = re.search(r"python3 (\S+summary\.py)", summary_script)
        assert called, "the step no longer runs the renderer"
        assert (self.REPO / called.group(1)).is_file()


class TestProductionIsTheOnlyTarget:
    """Both environments share this host, so they share the ledger, the
    watermark, the run lock, the rclone container name and the RC port. Two
    runs would advance each other's watermark, silently and in both
    directions. The job takes no environment input at all."""

    def steps_with_env(self, parsed: dict) -> list[dict]:
        return [s for s in parsed["jobs"]["mirror"]["steps"] if s.get("env")]

    def test_the_environment_name_is_a_constant(self, parsed: dict):
        assert parsed["env"]["ENV_NAME"] == "prod"

    def test_nothing_can_ask_for_another_environment(self, parsed: dict):
        # YAML reads a bare `on:` as the boolean true, so the key is not "on".
        inputs = parsed[True]["workflow_dispatch"].get("inputs") or {}
        assert not {"env", "environment", "env_name", "target"} & set(inputs), (
            f"the workflow offers an environment input: {sorted(inputs)}"
        )

    def test_every_step_that_names_an_environment_names_that_one(self, parsed: dict):
        for step in self.steps_with_env(parsed):
            named = step["env"].get("ENV_NAME")
            if named is not None:
                assert named == "${{ env.ENV_NAME }}", (
                    f"{step.get('name')!r} hardcodes an environment: {named}"
                )

    def test_the_deploy_path_comes_from_the_production_secret(self, parsed: dict):
        paths = {
            s["env"]["DEPLOY_PATH"]
            for s in self.steps_with_env(parsed)
            if "DEPLOY_PATH" in s["env"]
        }
        assert paths == {"${{ secrets.PROD_DEPLOY_PATH }}"}, paths
