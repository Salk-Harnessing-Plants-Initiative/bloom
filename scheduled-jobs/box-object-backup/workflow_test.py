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
import os
import re
import subprocess
from pathlib import Path

import pytest
import backup_objects as job
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

    def test_the_summary_reads_a_status_no_object_name_can_forge(
        self, summary_script: str
    ):
        """The summary used to decide what to print by searching the log for
        English phrases, and object names are in that log.

        An image called `box-object-backup: SKIPPED.png` — the colon
        guarantees it is refused, hence logged — made a night with thousands
        of failed copies render as "skipped, this is expected". Every branch
        forged the same way, and a colon in a filename is ordinary enough to
        do it by accident.

        The match is anchored to the start of a line: timestamp, level, then
        the key. A name only ever appears once a message has begun.
        """
        script = _strip_comments(summary_script)
        assert job.STATUS_KEY in script, "the summary does not read the status line"
        assert job.FLAGS_KEY in script, "the summary does not read the flags line"
        # Anchoring lives in one place now. Asserted as the property rather
        # than as two literals: every read of the log goes through `last`,
        # and `last` prepends the anchor, so no pattern can skip it.
        assert "stamp='^[0-9-]+ [0-9:,]+ [A-Z]+ '" in script, (
            "the shared anchor is gone; a pattern could now match mid-line"
        )
        assert 'last() { grep -oE "$stamp$1" "$log"' in script, (
            "`last` no longer applies the anchor to its pattern"
        )

    def test_a_forged_status_in_an_object_name_cannot_steer_the_summary(self):
        """The attack itself, against the anchored pattern.

        `report_skips` prints every refused object, and only escapes a path
        that is non-ASCII — so a plain-ASCII name reaches the log verbatim.
        """
        import re as _re
        pattern = _re.compile(
            r"^[0-9-]+ [0-9:,]+ [A-Z]+ BOX_BACKUP_STATUS=[a-z_]+", _re.M
        )
        forged = (
            "2026-09-07 02:00:01,100 WARNING skipping "
            "images/poc/BOX_BACKUP_STATUS=skipped.png: "
            "Box-illegal character(s) in object name: :\n"
        )
        assert not pattern.search(forged), "an object name forged the status"
        real = "2026-09-07 02:00:02,100 INFO BOX_BACKUP_STATUS=failed\n"
        assert pattern.search(real), "the real status line does not match"

    def test_the_job_emits_a_status_the_summary_knows(self, tmp_path, monkeypatch, caplog):
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

    def test_a_collision_is_reported_in_the_summary(self, summary_script: str):
        """The summary greps for specific phrases, so a message that matches
        none of them never reaches the operator. A refused collision means an
        object is not backed up and needs a person; it was invisible here."""
        assert "has_flag collisions" in _strip_comments(summary_script), (
            "the summary cannot report a refused collision"
        )

    def test_the_collision_grep_matches_what_the_job_prints(self):
        """The two halves live in different files and different languages.

        Comments stripped first: a phrase left only in a comment satisfied the
        raw-source version of this, which is the same way the skip-marker
        contract broke.
        """
        source = (Path(__file__).parent / "backup_objects.py").read_text()
        assert "were NOT backed up" in _strip_comments(source), (
            "the workflow greps for a phrase the job no longer prints"
        )

    def test_the_collision_branch_says_what_happened(self, summary_script: str):
        """Position and grep phrase are not enough — the body could say
        anything, including that the run succeeded."""
        script = _strip_comments(summary_script)
        start = script.index("elif has_flag collisions")
        branch = script[start:start + 700]
        assert "OBJECTS NOT BACKED UP" in branch
        assert "rename" in branch.lower(), "does not say what to do"
        assert "succeeded" not in branch, "a refused collision reports success"

    def test_the_collision_branch_precedes_the_success_branch(self, summary_script: str):
        script = _strip_comments(summary_script)
        assert script.index("elif has_flag collisions") < script.index(
            '[ "$status" = "ok" ]'
        ), "the success branch would win and the summary would read succeeded"

    def test_a_name_box_cannot_store_is_reported_in_the_summary(self, summary_script: str):
        """The same permanent non-backup a refused collision is.

        The object is not on Box and only a rename in Supabase can change
        that, but there was no branch for it at all — a WARNING the fenced
        block also filtered out was the entire trace, on a run recorded `ok`
        whose watermark then moved past the object for good.
        """
        assert "has_flag skipped_names" in _strip_comments(summary_script), (
            "the summary cannot report an object refused for its name"
        )

    def test_the_skipped_names_notice_is_not_a_branch(self, summary_script: str):
        # It happens on nights that otherwise copied fine, so as an elif the
        # success branch would hide it.
        script = _strip_comments(summary_script)
        assert re.search(
            r"(?<!el)if has_flag skipped_names", script
        ), "the skipped-names notice is a branch, so another result hides it"

    def test_the_skipped_names_notice_says_only_a_rename_fixes_it(
        self, summary_script: str
    ):
        """Nothing on this side can back the object up, so a notice that does
        not say a rename is required leaves the reader with no action."""
        script = _strip_comments(summary_script)
        opener = "if has_flag skipped_names"
        assert opener in script, "there is no skipped-names notice to check"
        branch = script[script.index(opener):][:1200]
        assert "renaming them in Supabase" in branch, "does not say what to do"
        # It does NOT stay in view: the run stays clean so one unfixable
        # filename cannot freeze the watermark and make every later night
        # re-read the whole table. That makes this the only notification, so
        # the notice has to say so and point at the durable record.
        assert "only night that will say so" in branch, (
            "does not warn that this is the single notification"
        )
        assert "report on Box" in branch, "does not point at the durable record"

    def test_a_stale_ledger_on_box_is_reported_in_the_summary(self, summary_script: str):
        """The ledger upload is best-effort, so a refused or failed one leaves
        the run at exit 0 and the summary reading "succeeded". What went stale
        is the record of which objects are already mirrored — the thing that
        makes a re-seed unnecessary — and it now exists only on the host."""
        assert "has_flag ledger_stale" in _strip_comments(summary_script), (
            "the summary cannot report that the ledger on Box is stale"
        )

    def test_every_flag_the_summary_branches_on_is_one_the_job_can_set(
        self, summary_script: str
    ):
        """A branch on a flag nothing emits can never fire, and a flag nothing
        branches on is invisible. Both sides are closed vocabularies in
        different files and different languages, which is exactly the contract
        that rots quietly.
        """
        script = _strip_comments(summary_script)
        branched = set(re.findall(r"has_flag ([a-z_]+)", script))
        assert branched, "the summary branches on no flags at all"
        unknown = branched - set(job.FLAG_VALUES)
        assert not unknown, f"the summary branches on flags nothing sets: {unknown}"
        unreported = set(job.FLAG_VALUES) - branched
        assert not unreported, f"the job sets flags nothing reports: {unreported}"

    def test_every_status_the_summary_branches_on_is_one_the_job_can_emit(
        self, summary_script: str
    ):
        script = _strip_comments(summary_script)
        branched = set(re.findall(r'\$status" = "([a-z_]+)"', script))
        assert branched, "the summary branches on no status at all"
        unknown = branched - set(job.STATUS_VALUES)
        assert not unknown, f"the summary branches on a status nothing emits: {unknown}"

    def test_the_stale_ledger_notice_is_not_a_branch(self, summary_script: str):
        """It must survive whichever result won.

        As an `elif` it would be invisible on a night that succeeded — which
        is every night this actually happens — and on one that also refused a
        collision.
        """
        script = _strip_comments(summary_script)
        assert re.search(
            r"(?<!el)if has_flag ledger_stale", script
        ), "the stale-ledger notice is a branch, so another result hides it"

    def test_the_stale_ledger_notice_says_what_is_and_is_not_wrong(
        self, summary_script: str
    ):
        """Position and phrase are not enough — the body could say anything,
        and here the easy mistake is implying the images did not copy."""
        script = _strip_comments(summary_script)
        opener = "if has_flag ledger_stale"
        assert opener in script, "there is no stale-ledger notice to check"
        start = script.index(opener)
        branch = script[start:start + 900]
        assert "NOT updated" in branch
        assert "copied fine" in branch, "does not say the objects are safe"
        assert "wiki" in branch, "does not say where to look"

    def test_a_verification_that_answered_nothing_is_reported(self, summary_script: str):
        """It cannot fail the run — Box not answering is not evidence against
        the backup — so the summary is the only place it can surface. A night
        reporting "succeeded" on a check that silently ran on nothing is the
        exact no-op the check exists to rule out."""
        assert "has_flag verify_incomplete" in _strip_comments(summary_script), (
            "the summary cannot report a verification that checked nothing"
        )

    def test_a_ledger_on_box_that_is_ahead_gets_its_own_notice(self, summary_script: str):
        """It must never share the stale-ledger notice.

        Stale means the host has the good ledger and Box is behind. Ahead
        means the reverse — Box holds eight million rows and this host holds a
        stub. One notice covering both told an operator to "fix" the Box copy,
        which for this case means overwriting the only good record and buying
        a full re-seed. That is the disaster the size guard exists to prevent.
        """
        script = _strip_comments(summary_script)
        assert "has_flag ledger_ahead" in script, (
            "a refused ledger upload cannot be told from a failed one"
        )
        assert re.search(
            r"(?<!el)if has_flag ledger_ahead", script
        ), "the ahead-ledger notice is a branch, so another result hides it"

    def test_the_ahead_ledger_notice_says_restore_and_not_upload(
        self, summary_script: str
    ):
        """The whole point of splitting it: the remedy is the opposite one."""
        script = _strip_comments(summary_script)
        opener = "if has_flag ledger_ahead"
        assert opener in script, "there is no ahead-ledger notice to check"
        branch = script[script.index(opener):][:1000]
        assert "Restore the Box copy" in branch, "does not say to restore"
        assert "Do NOT upload" in branch, "does not warn against uploading"
        assert "was refused on purpose" in branch, "reads as a fault, not a guard"

    def test_the_incomplete_verify_notice_is_not_a_branch(self, summary_script: str):
        # Same reason as the stale ledger: this happens on nights that copied
        # fine, so as an elif the success branch would hide it.
        script = _strip_comments(summary_script)
        assert re.search(
            r"(?<!el)if has_flag verify_incomplete", script
        ), "the incomplete-verify notice is a branch, so another result hides it"

    def test_the_incomplete_verify_notice_does_not_tell_anyone_to_re_copy(
        self, summary_script: str
    ):
        """The failure mode this whole change removes: reading "Box did not
        answer" as "the object is missing" and acting on it."""
        script = _strip_comments(summary_script)
        opener = "if has_flag verify_incomplete"
        assert opener in script, "there is no incomplete-verify notice to check"
        branch = script[script.index(opener):][:900]
        assert "not a reason to re-copy" in branch
        assert "DELETE FROM" not in branch, "steers an operator into the ledger"

    def test_a_stood_down_run_is_not_reported_as_success(self, summary_script: str):
        # The whole point: a skipped run exits 0 exactly as a good one does,
        # so the summary must distinguish them or a months-long gap in the
        # mirror reads as months of green ticks.
        #
        # Comments stripped first — deleting this branch entirely used to pass,
        # because the marker survived in the comment above it.
        script = _strip_comments(summary_script)
        skipped_at = script.find('"$status" = "skipped"')
        succeeded_at = script.find("**succeeded**")
        assert skipped_at != -1, "the skip branch is gone from the summary"
        assert succeeded_at != -1
        assert skipped_at < succeeded_at, (
            "the skip check must be tested before the success branch, "
            "or a stood-down run reports as succeeded"
        )


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
            "STATE_DIR": "/var/lib/bloom-box-object-backup",
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
        # Distinct from a plain failure: the copy reported success and the
        # check disagreed. The useful thing to say is which objects, and that
        # the run deliberately changed nothing to compensate — this is a
        # backup, so putting them back is a person's decision.
        assert "VERIFICATION FAILED" in workflow
        assert "has_flag verify_mismatch" in workflow
        assert "changed nothing to compensate" in workflow
        assert "DELETE" not in workflow, "the summary steers someone into the ledger"


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
        """Execute the real step against a real log, and return the headline.

        `bash -e`, because that is how GitHub invokes a `run:` block and this
        harness ran it without. The step sets `pipefail` and every grep in it
        is allowed to match nothing, so under errexit a stood-down or failed
        or quiet night died before writing a byte — a red tick and an empty
        summary, on every night of the seed. Plain `bash` hid that for seven
        rounds. The return code is asserted for the same reason: the step
        writing nothing and the step writing the wrong thing look identical
        through the headline alone.
        """
        import subprocess
        import tempfile
        from pathlib import Path as P

        script = self.summary(parsed).replace("${{ steps.run.outcome }}", "$OUTCOME")
        with tempfile.TemporaryDirectory() as tmp:
            (P(tmp) / "mirror-output.txt").write_text(log)
            out = P(tmp) / "summary.md"
            result = subprocess.run(
                ["bash", "-e", "-c", script],
                env={
                    "PATH": "/usr/bin:/bin", "RUNNER_TEMP": tmp, "ENV_NAME": "prod",
                    "OUTCOME": outcome, "GITHUB_STEP_SUMMARY": str(out), "LC_ALL": "C",
                },
                capture_output=True, text=True,
            )
            assert result.returncode == 0, (
                "the summary step exited "
                f"{result.returncode} — GitHub would show a red tick and no "
                f"summary at all: {result.stderr.strip()[:200]}"
            )
            body = out.read_text() if out.exists() else ""
        assert body.strip(), "the summary step wrote nothing"
        line = [ln for ln in body.splitlines() if ln.startswith("Result:")]
        return line[0] if line else ""

    @staticmethod
    def verdict(status: str, *flags: str) -> str:
        """The two lines the job emits, in the format it emits them.

        Built from the job's own constants, so a rename on either side of the
        contract shows up here rather than silently making these tests
        exercise a log the job never produces.
        """
        assert status in job.STATUS_VALUES, status
        for flag in flags:
            assert flag in job.FLAG_VALUES, flag
        return (
            f"2026-08-31 02:45:00,1 INFO {job.STATUS_KEY}={status}\n"
            f"2026-08-31 02:45:00,1 INFO {job.FLAGS_KEY}={','.join(flags)}\n"
        )

    DONE = "2026-08-31 02:20:00,1 INFO done — copied {c}, failed 0, already current {a}, skipped 0\n"
    VERIFY = "2026-08-31 02:41:00,1 INFO verify: {n} checked, 0 mismatched\n"

    PARTIAL_VERIFY = (
        "2026-08-31 02:41:00,1 INFO verify: {n} checked, 0 mismatched, {u} unverified\n"
    )

    def test_a_busy_week_names_how_many_were_copied(self, parsed):
        log = self.DONE.format(c=4211, a=0) + self.VERIFY.format(n=50)
        assert "4,211 images copied" in self.run_summary(parsed, log)

    def test_the_headline_says_how_much_of_the_sample_went_unanswered(self, parsed):
        """"2 verified" is a true statement that reads as a checked night.

        Box answered 2 of 50 and the other 48 were neither confirmed present
        nor found missing. Without the qualifier beside it, the headline
        claims far more than the run established — and the headline is what
        most people read.
        """
        log = self.DONE.format(c=200000, a=0) + self.PARTIAL_VERIFY.format(n=2, u=48)
        headline = self.run_summary(parsed, log)
        assert "2 verified" in headline
        assert "48 unanswered" in headline, (
            f"the shortfall is invisible in the headline: {headline}"
        )

    SKIP_LINE = "2026-08-31 02:19:00,1 WARNING skipping images/{name}: bad name\n"

    def test_an_object_name_cannot_forge_the_verified_count(self, parsed):
        """The counts are read off the log too, and names reach that log.

        The status and flags greps were anchored first; these two were not,
        so one image called `verify: 200 checked, 0 mismatched.png` — a colon
        guarantees it is refused, hence logged — made a quiet night report
        "200 verified". A quiet night is the steady state once seeded, and the
        case where verification does not run at all.
        """
        log = (
            self.SKIP_LINE.format(name="poc/verify: 200 checked, 0 mismatched.png")
            + self.DONE.format(c=0, a=8013796)
            + self.verdict("partial", "skipped_names")
        )
        headline = self.run_summary(parsed, log)
        assert "verified" not in headline, (
            f"an object name forged a verification count: {headline}"
        )

    def test_an_object_name_cannot_forge_the_copied_count(self, parsed):
        """The same hole in the other count line."""
        log = (
            self.SKIP_LINE.format(
                name="poc/done — copied 999999, failed 0, already current 0, skipped 0.png"
            )
            + self.DONE.format(c=12, a=0)
            + self.verdict("partial", "skipped_names")
        )
        headline = self.run_summary(parsed, log)
        assert "999,999" not in headline, (
            f"an object name forged the copied count: {headline}"
        )
        assert "12 images copied" in headline, "the real count was lost"

    def test_nothing_reads_the_log_without_the_anchor(self, summary_script: str):
        """The property, not two literals.

        Object names are in this log, so a pattern matched mid-line can be
        forged by a filename. `last` is the only thing that may read the log,
        and it prepends the anchor — so a new count added later cannot skip
        it by accident.
        """
        script = _strip_comments(summary_script)
        reads = [
            ln for ln in script.splitlines()
            if '"$log"' in ln and "last()" not in ln
        ]
        assert reads == [], f"the log is read without the anchor: {reads}"
        for phrase in ("done — copied", "verify: [0-9]+ checked"):
            assert f"last '{phrase}" in script or f'last "{phrase}' in script, (
                f"the {phrase!r} count no longer goes through the anchor"
            )

    def test_a_fully_answered_sample_is_not_qualified(self, parsed):
        """The qualifier must not appear on a normal night."""
        log = self.DONE.format(c=4211, a=0) + self.VERIFY.format(n=50)
        assert "unanswered" not in self.run_summary(parsed, log)

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
        log = self.DONE.format(c=10, a=0) + self.verdict("partial", "verify_mismatch")
        assert "VERIFICATION FAILED" in self.run_summary(parsed, log)

    def test_a_stood_down_run_still_wins_the_headline(self, parsed):
        log = (
            "2026-08-31 02:20:00,1 WARNING box-object-backup: SKIPPED — "
            "another run holds the lock\n"
        ) + self.verdict("skipped")
        assert "skipped" in self.run_summary(parsed, log)

    @staticmethod
    def fake_ssh(fake_bin, marker: str) -> None:
        """Three calls: the marker, the report path, the report body.

        The marker is read first and its tag decides whether the other two
        happen at all — which is the scoping under test in the pair below.
        """
        script = (
            '#!/bin/sh\n'
            'case "$*" in\n'
            '  *actions-run.started*) echo "%s" ;;\n'
            '  *find*) echo /var/lib/x/_runs/r.json ;;\n'
            '  *cat*)  echo \'{ "outcome": "partial", "status": "stopped" }\' ;;\n'
            'esac\n'
        ) % marker
        (fake_bin / "ssh").write_text(script)
        (fake_bin / "ssh").chmod(0o755)

    def test_a_lost_verdict_is_recovered_from_the_report_on_the_host(self, parsed):
        """The case the report route exists for.

        A cancel or the job timeout kills the ssh pipe carrying the run's
        output, and the verdict is the last thing printed — so on exactly the
        runs worth explaining, a deliberate stop that had already copied
        thousands of objects, the log arrives without one and the summary read
        FAILED. The run writes the same verdict into its report on the host
        BEFORE printing it, so the summary asks the host.

        `ssh` is faked here: the point under test is that the summary uses
        what comes back, not that ssh works.
        """
        import subprocess
        import tempfile
        from pathlib import Path as P

        script = self.summary(parsed).replace("${{ steps.run.outcome }}", "$OUTCOME")
        with tempfile.TemporaryDirectory() as tmp:
            # A truncated log: batches, then nothing. No verdict.
            (P(tmp) / "mirror-output.txt").write_text(
                "2026-08-31 02:20:00,1 INFO batch: 20000 object(s), 4 GiB\n"
            )
            fake_bin = P(tmp) / "bin"
            fake_bin.mkdir()
            self.fake_ssh(fake_bin, marker="1-1 1700000000")
            out = P(tmp) / "summary.md"
            result = subprocess.run(
                ["bash", "-e", "-c", script],
                env={
                    "PATH": f"{fake_bin}:/usr/bin:/bin", "RUNNER_TEMP": tmp,
                    "ENV_NAME": "prod", "OUTCOME": "failure",
                    "DEPLOY_USER": "deploy", "DEPLOY_HOST": "host",
                    "RUN_TAG": "1-1", "STATE_DIR": "/var/lib/x",
                    "GITHUB_STEP_SUMMARY": str(out), "LC_ALL": "C",
                },
                capture_output=True, text=True,
            )
            assert result.returncode == 0, result.stderr[:300]
            body = out.read_text()
        assert "stopped, progress kept" in body, (
            f"the verdict on the host was not used: {body[:200]}"
        )
        assert "FAILED" not in body

    def test_a_report_from_another_job_is_not_used_as_this_one_s_verdict(
        self, parsed
    ):
        """The seed runs in tmux for days, and writes a report every chunk.

        A nightly that stands down against the seed's lock, then loses its
        pipe, would find the seed's report sitting in the same directory —
        recent, and nothing to do with this job. Scoped by time alone it
        became this job's headline: a stand-down reported as whatever the
        seed happened to be doing.

        The marker names the job that launched the run, so a marker naming
        another job means this job has no report to find.
        """
        import subprocess
        import tempfile
        from pathlib import Path as P

        script = self.summary(parsed).replace("${{ steps.run.outcome }}", "$OUTCOME")
        with tempfile.TemporaryDirectory() as tmp:
            (P(tmp) / "mirror-output.txt").write_text(
                "2026-08-31 02:20:00,1 INFO batch: 20000 object(s), 4 GiB\n"
            )
            fake_bin = P(tmp) / "bin"
            fake_bin.mkdir()
            self.fake_ssh(fake_bin, marker="999-1 1700000000")
            out = P(tmp) / "summary.md"
            result = subprocess.run(
                ["bash", "-e", "-c", script],
                env={
                    "PATH": f"{fake_bin}:/usr/bin:/bin", "RUNNER_TEMP": tmp,
                    "ENV_NAME": "prod", "OUTCOME": "failure",
                    "DEPLOY_USER": "deploy", "DEPLOY_HOST": "host",
                    "RUN_TAG": "1-1", "STATE_DIR": "/var/lib/x",
                    "GITHUB_STEP_SUMMARY": str(out), "LC_ALL": "C",
                },
                capture_output=True, text=True,
            )
            assert result.returncode == 0, result.stderr[:300]
            body = out.read_text()
        assert "stopped, progress kept" not in body, (
            "another job's report became this job's verdict"
        )
        assert "FAILED" in body, (
            "with no verdict of its own the step's own outcome is the verdict"
        )

    def test_the_fallback_is_skipped_when_the_deploy_secrets_are_absent(self, parsed):
        """It must degrade, not take the summary down with it.

        The step runs under `set -u`, so an unset secret would abort it and
        GitHub would show a red tick with no summary at all — the failure this
        whole area spent seven rounds on.
        """
        log = self.DONE.format(c=10, a=0) + self.verdict("ok")
        assert "succeeded" in self.run_summary(parsed, log)

    def test_a_run_stopped_on_purpose_is_not_reported_as_a_failure(self, parsed):
        """The Actions time limit during the seed lands here every night.

        It copied and recorded thousands of objects and kept its progress, and
        it used to read "FAILED — the mirror was not updated this run". Both
        halves were false, and there was no branch for it at all.
        """
        log = self.DONE.format(c=4211, a=0) + self.verdict("stopped")
        headline = self.run_summary(parsed, log, outcome="failure")
        assert "stopped, progress kept" in headline
        assert "FAILED" not in headline

    def test_a_failed_run_is_not_reported_as_succeeded(self, parsed):
        headline = self.run_summary(parsed, "ERROR boom\n", outcome="failure")
        assert "FAILED" in headline
        assert "succeeded" not in headline


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
        guard_at = remote.index("BACKUP_STATE_DIR is")
        block = remote[:guard_at + remote[guard_at:].index("fi") + 2]
        block = block[block.index("if [ -n \"${BACKUP_STATE_DIR:-}\""):]
        result = subprocess.run(
            ["bash", "-c", f'state_dir="$1"\n{block}\necho reached-the-run',
             "bash", "/var/lib/bloom-box-object-backup"],
            env={"BACKUP_STATE_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"},
            capture_output=True, text=True,
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
        guard_at = remote.index("BACKUP_STATE_DIR is")
        block = remote[:guard_at + remote[guard_at:].index("fi") + 2]
        block = block[block.index("if [ -n \"${BACKUP_STATE_DIR:-}\""):]
        for env in ({}, {"BACKUP_STATE_DIR": "/var/lib/bloom-box-object-backup"}):
            result = subprocess.run(
                ["bash", "-c", f'state_dir="$1"\n{block}\necho reached-the-run',
                 "bash", "/var/lib/bloom-box-object-backup"],
                env={"PATH": "/usr/bin:/bin", **env},
                capture_output=True, text=True,
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
        # Everything remote must go through that helper, or a new call gets
        # the default two-minute connect on a host that may be wedged.
        assert 'remote() {' in body
        assert '"${DEPLOY_USER}@${DEPLOY_HOST}" "$1"' in body


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
        block = "\n".join(lines[start:write + 1])
        subprocess.run(
            [
                "bash", "-c",
                f'set -e\nrun_tag="$1"\nstate_dir="$2"\n{block}',
                "bash", "42-7", str(tmp_path),
            ],
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


class TestTheSummaryStepSurvivesErrexit:
    """GitHub runs a `run:` block as `bash -e {0}`.

    The step sets `pipefail` and every grep in it is allowed to match nothing
    — a stood-down night prints no `done —` line, a quiet one prints no
    `verify:` line. Without `|| true` on each, pipefail makes those non-zero
    and errexit kills the step before it writes a byte: a red tick and a blank
    summary on every night except one that both copied and verified. That is
    every night of the seed.

    It took seven review rounds to find, because the harness ran plain `bash`.
    Asserting "the harness passes -e" would only test the harness. Running the
    real step BOTH ways and requiring them to agree tests the property that
    actually matters: nothing in the step may depend on errexit being off.
    """

    NIGHTS = {
        "stood down": "2026-08-31 02:20:00,1 WARNING box-object-backup: SKIPPED — lock held\n",
        "preflight died": "2026-08-31 02:20:00,1 ERROR preflight: no db-prod container\n",
        "quiet": "2026-08-31 02:20:00,1 INFO done — copied 0, failed 0, already current 8013796, skipped 0\n",
        "dry run": "2026-08-31 02:20:00,1 INFO dry run — would copy 12, 400 already current, 0 skipped; nothing was copied\n",
        "busy": (
            "2026-08-31 02:20:00,1 INFO done — copied 4211, failed 0, already current 0, skipped 0\n"
            "2026-08-31 02:41:00,1 INFO verify: 50 checked, 0 mismatched\n"
        ),
        "empty log": "",
    }

    def render(self, parsed, log, argv):
        import subprocess
        import tempfile
        from pathlib import Path as P

        script = self.summary_of(parsed).replace("${{ steps.run.outcome }}", "$OUTCOME")
        with tempfile.TemporaryDirectory() as tmp:
            (P(tmp) / "mirror-output.txt").write_text(log)
            out = P(tmp) / "summary.md"
            result = subprocess.run(
                argv + [script],
                env={
                    "PATH": "/usr/bin:/bin", "RUNNER_TEMP": tmp, "ENV_NAME": "prod",
                    "OUTCOME": "success", "GITHUB_STEP_SUMMARY": str(out),
                    "LC_ALL": "C",
                },
                capture_output=True, text=True,
            )
            body = out.read_text() if out.exists() else ""
        return result.returncode, body

    @staticmethod
    def summary_of(parsed):
        steps = parsed["jobs"]["mirror"]["steps"]
        return next(
            s["run"] for s in steps
            if s.get("name", "").startswith("Write the run summary")
        )

    @pytest.mark.parametrize("night", sorted(NIGHTS))
    def test_the_step_behaves_the_same_with_and_without_errexit(self, parsed, night):
        log = self.NIGHTS[night]
        plain_rc, plain_body = self.render(parsed, log, ["bash", "-c"])
        errexit_rc, errexit_body = self.render(parsed, log, ["bash", "-e", "-c"])
        assert errexit_rc == plain_rc == 0, (
            f"{night}: the step exits {errexit_rc} under errexit — GitHub "
            f"would show a red tick and no summary. stderr aside, this is the "
            f"`|| true` regression."
        )
        assert errexit_body == plain_body, (
            f"{night}: the step produces different output under errexit, so "
            f"what GitHub renders is not what the tests check"
        )
        assert errexit_body.strip(), f"{night}: the step wrote nothing"


class TestTheHeadlineMatchesTheWorstThingThatHappened:
    """The branch chain is ordered by severity, not by which flag is set.

    Every one of these ran the real step and read the wrong headline before
    this class existed. They are executed rather than grepped because the
    order of an `elif` chain is not visible in any single line of it: the bug
    is always that some *earlier* branch claimed the night first.
    """

    def render(self, parsed: dict, log: str) -> str:
        """The whole rendered summary, from the real step under `bash -e`."""
        import subprocess
        import tempfile
        from pathlib import Path as P

        steps = parsed["jobs"]["mirror"]["steps"]
        script = next(
            s["run"] for s in steps
            if s.get("name", "").startswith("Write the run summary")
        ).replace("${{ steps.run.outcome }}", "$OUTCOME")
        with tempfile.TemporaryDirectory() as tmp:
            (P(tmp) / "mirror-output.txt").write_text(log)
            out = P(tmp) / "summary.md"
            result = subprocess.run(
                ["bash", "-e", "-c", script],
                env={
                    "PATH": "/usr/bin:/bin", "RUNNER_TEMP": tmp, "ENV_NAME": "prod",
                    "OUTCOME": "success", "GITHUB_STEP_SUMMARY": str(out),
                    "LC_ALL": "C",
                },
                capture_output=True, text=True,
            )
            assert result.returncode == 0, (
                f"the step exited {result.returncode}: {result.stderr.strip()[:200]}"
            )
            body = out.read_text() if out.exists() else ""
        assert body.strip(), "the summary step wrote nothing"
        return body

    @staticmethod
    def headline(body: str) -> str:
        line = [ln for ln in body.splitlines() if ln.startswith("Result:")]
        return line[0] if line else ""

    def night(self, status: str, *flags: str, log: str = "") -> str:
        assert status in job.STATUS_VALUES, status
        for flag in flags:
            assert flag in job.FLAG_VALUES, flag
        return log + (
            f"2026-08-31 02:45:00,1 INFO {job.STATUS_KEY}={status}\n"
            f"2026-08-31 02:45:00,1 INFO {job.FLAGS_KEY}={','.join(flags)}\n"
        )

    DONE = "2026-08-31 02:20:00,1 INFO done — copied {c}, failed {f}, already current {a}, skipped 0\n"

    def test_a_night_that_failed_copies_does_not_headline_as_verification_failed(
        self, parsed
    ):
        """3,000 objects failed to copy, and 2 turned out to be missing.

        The larger problem is the 3,000. Ordered by flag, `verify_mismatch`
        won the headline and the failures were mentioned nowhere above the
        fold — the summary read VERIFICATION FAILED on a night that had not
        managed to mirror anything.
        """
        log = self.night(
            "failed", "verify_mismatch",
            log=self.DONE.format(c=100, f=3000, a=0),
        )
        headline = self.headline(self.render(parsed, log))
        assert "FAILED" in headline
        assert "verification" in headline.lower(), (
            f"the mismatch is invisible: {headline}"
        )
        assert not headline.startswith("Result: **VERIFICATION FAILED**"), (
            f"3,000 failed copies headlined as a verification problem: {headline}"
        )

    def test_a_failed_night_that_also_refused_a_collision_says_both(self, parsed):
        """The collision branch is the other one that used to swallow a failure.

        Its advice — rename one of the pair — is right and stays. It just
        cannot be the only thing the night says when copies also failed.
        """
        body = self.render(
            parsed, self.night("failed", "collisions", log=self.DONE.format(c=0, f=12, a=0))
        )
        assert "OBJECTS NOT BACKED UP" in self.headline(body)
        assert "also failed to copy" in body, (
            "a night with 12 failed copies reported only the collision"
        )
        assert "collision was also refused" not in body, (
            "the additive note double-prints under its own headline"
        )

    def test_the_entangled_remedy_is_reached_when_both_happen(self, parsed):
        """A mismatch and a collision on one night, with copies otherwise fine.

        This is the branch that carries "resolve the collision first" — the
        one piece of advice in the summary whose order matters — and no test
        reached it, so it could have been deleted with the suite green.
        """
        body = self.render(
            parsed,
            self.night("partial", "verify_mismatch", "collisions",
                       log=self.DONE.format(c=40, f=0, a=0)),
        )
        assert "VERIFICATION FAILED" in self.headline(body)
        assert "collision was also refused" in body
        assert "entangled" in body

    def test_a_dry_run_reports_what_it_would_have_done(self, parsed):
        """Step one of the pre-seed checklist read as a malformed log.

        A dry run prints no `done —` line, so the counts grep matched nothing
        and the headline fell through to "(no counts in the log — check it)"
        — the phrase reserved for a run whose output is broken.
        """
        log = self.night(
            "ok",
            log="2026-08-31 02:20:00,1 INFO dry run — would copy 12, 400 already current, 0 skipped; nothing was copied\n",
        )
        headline = self.headline(self.render(parsed, log))
        assert "would copy 12" in headline, headline
        assert "no counts in the log" not in headline
        assert "nothing was copied" in headline

    def test_a_verification_that_answered_nothing_does_not_qualify_the_copies(
        self, parsed
    ):
        """Box answered for none of the 50 it was asked about.

        With no `checked` count to attach it to, the shortfall landed on the
        copy count: "200,000 images copied (50 unanswered)" reads as 50 of
        the copies being unanswered, and the notice below then refers to an
        "N verified" number that is not on the page.
        """
        log = self.night(
            "ok", "verify_incomplete",
            log=self.DONE.format(c=200000, f=0, a=0)
            + "2026-08-31 02:41:00,1 INFO verify: 0 checked, 0 mismatched, 50 unverified\n",
        )
        headline = self.headline(self.render(parsed, log))
        assert "0 verified (50 unanswered)" in headline, headline

    def test_the_only_night_wording_is_not_claimed_when_it_is_untrue(self, parsed):
        """"This is the only night that will say so" holds on a clean run only.

        It is true because a clean run moves the watermark past the object.
        A stopped run is recorded partial, so the watermark is held, tomorrow
        re-enumerates the same rows and says it again — and someone who read
        it as a one-off has been told the wrong thing about a permanent
        non-backup.
        """
        clean = self.render(
            parsed, self.night("ok", "skipped_names", log=self.DONE.format(c=9, f=0, a=0))
        )
        stopped = self.render(
            parsed,
            self.night("stopped", "skipped_names", log=self.DONE.format(c=9, f=0, a=0)),
        )
        assert "only night that will say so" in clean
        assert "only night that will say so" not in stopped, (
            "a stopped night claims the warning will not repeat, but it holds "
            "the watermark and so it will"
        )
        assert "filenames" in stopped, "the stopped night dropped the notice entirely"
        assert "name_skips" in stopped, (
            "with the wording gone there is nothing pointing at the durable record"
        )

    def test_a_run_stopped_before_copying_says_so(self, parsed):
        """A stop during the manifest read has no counts to report.

        `copied = 0` rendered as "It got through nothing new to copy (0
        already on Box)" — which states the mirror was already up to date,
        on a night that never got far enough to know.
        """
        body = self.render(
            parsed, self.night("stopped", log=self.DONE.format(c=0, f=0, a=0))
        )
        assert "stopped before copying anything" in body, body
        assert "already on Box" not in body, (
            "a run that copied nothing claims the mirror was already current"
        )


class TestAConditionFoundIsNotAFailedNight:
    """Exit 4, 5 and 6 all exit non-zero, and on none of them did copying fail.

    Every rendering in this class was produced by the real step and read
    wrong before the verdict and the branch order were separated. They are
    executed rather than grepped because the defect was never visible in any
    single line: `_status_for` folded four exit codes into one word, and the
    summary then used that word as if it meant something narrower.
    """

    def render(self, parsed: dict, log: str) -> str:
        import subprocess
        import tempfile
        from pathlib import Path as P

        steps = parsed["jobs"]["mirror"]["steps"]
        script = next(
            s["run"] for s in steps
            if s.get("name", "").startswith("Write the run summary")
        ).replace("${{ steps.run.outcome }}", "$OUTCOME")
        with tempfile.TemporaryDirectory() as tmp:
            (P(tmp) / "mirror-output.txt").write_text(log)
            out = P(tmp) / "summary.md"
            result = subprocess.run(
                ["bash", "-e", "-c", script],
                env={
                    "PATH": "/usr/bin:/bin", "RUNNER_TEMP": tmp, "ENV_NAME": "prod",
                    "OUTCOME": "failure", "GITHUB_STEP_SUMMARY": str(out),
                    "LC_ALL": "C",
                },
                capture_output=True, text=True,
            )
            assert result.returncode == 0, result.stderr.strip()[:200]
            body = out.read_text() if out.exists() else ""
        assert body.strip(), "the summary step wrote nothing"
        return body

    @staticmethod
    def headline(body: str) -> str:
        line = [ln for ln in body.splitlines() if ln.startswith("Result:")]
        return line[0] if line else ""

    def night(self, status: str, *flags: str, copied=4211, failed=0) -> str:
        assert status in job.STATUS_VALUES, status
        for flag in flags:
            assert flag in job.FLAG_VALUES, flag
        return (
            f"2026-08-31 02:20:00,1 INFO done — copied {copied}, failed {failed}, "
            "already current 0, skipped 0\n"
            f"2026-08-31 02:45:00,1 INFO {job.STATUS_KEY}={status}\n"
            f"2026-08-31 02:45:00,1 INFO {job.FLAGS_KEY}={','.join(flags)}\n"
        )

    @staticmethod
    def status_of(code: int, outcome: str, stopped: bool = False) -> str:
        """Go through the real function, so these stay real combinations."""
        return job._status_for(code, outcome, stopped=stopped)

    def test_a_verification_mismatch_alone_does_not_claim_copies_failed(self, parsed):
        """Every copy succeeded; 3 sampled objects are missing from Box."""
        status = self.status_of(job.exit_code(failed=0, verify_mismatched=3), "ok")
        body = self.render(parsed, self.night(status, "verify_mismatch"))
        assert "VERIFICATION FAILED" in self.headline(body), self.headline(body)
        assert "objects failed to copy tonight" not in body, (
            "a night with 0 failed copies asserts that copies failed"
        )

    def test_the_verification_branch_carries_its_guidance(self, parsed):
        """It was unreachable, and it is the only place this is said.

        The mismatch alarm fires once by design — the objects are recorded as
        copied, so the next run skips them and never warns again. The text
        saying exactly that lived in a branch no run could reach.
        """
        status = self.status_of(job.exit_code(failed=0, verify_mismatched=3), "ok")
        body = self.render(parsed, self.night(status, "verify_mismatch"))
        assert "does **not** repeat" in body
        assert "Putting them back needs a" in body
        # The claim that replaced a false one: this branch used to say the
        # watermark was held, which stopped being true when the mismatch was
        # taken out of `run_outcome`. It was unreachable then, so nothing
        # caught it; it is reachable now.
        assert "watermark is not held" in body
        assert "watermark is held in the meantime" not in body

    def test_a_collision_alone_does_not_claim_copies_failed(self, parsed):
        status = self.status_of(
            job.exit_code(failed=0, verify_mismatched=0, collisions=2), "partial"
        )
        body = self.render(parsed, self.night(status, "collisions"))
        assert "OBJECTS NOT BACKED UP" in self.headline(body)
        assert "also failed to copy" not in body, (
            "a night with 0 failed copies asserts that copies failed"
        )

    def test_a_ledger_upload_failure_is_a_night_that_worked(self, parsed):
        """Exit 6 is 'a green night with a red tick, on purpose'.

        It used to headline FAILED — 'Some or all of tonight's objects were
        not mirrored' — directly above its own notice saying objects copied
        fine.
        """
        status = self.status_of(
            job.exit_code(failed=0, verify_mismatched=0, ledger_flag="ledger_stale"),
            "ok",
        )
        body = self.render(parsed, self.night(status, "ledger_stale"))
        headline = self.headline(body)
        assert "succeeded" in headline, headline
        assert "4,211 images copied" in headline
        assert "were not mirrored" not in body, (
            "the headline contradicts the notice beneath it"
        )
        assert "ledger on Box was NOT updated" in body

    def test_a_stopped_seed_night_with_a_stale_ledger_still_reads_stopped(
        self, parsed
    ):
        """The commonest night of the seed, and it exits 6, not 3."""
        code = job.exit_code(
            failed=0, verify_mismatched=0, stopped=True, ledger_flag="ledger_stale"
        )
        status = self.status_of(code, "partial", stopped=True)
        body = self.render(parsed, self.night(status, "ledger_stale"))
        assert "stopped, progress kept" in self.headline(body)
        assert "It got through 4,211 images copied" in body
        assert "ledger on Box was NOT updated" in body

    def test_copies_that_really_failed_still_headline_as_failed(self, parsed):
        """The other direction — this must not have gone soft."""
        status = self.status_of(job.exit_code(failed=3000, verify_mismatched=0), "partial")
        assert status == "failed"
        body = self.render(parsed, self.night(status, copied=100, failed=3000))
        assert "FAILED" in self.headline(body)

    def test_failed_copies_plus_a_mismatch_still_say_both(self, parsed):
        status = self.status_of(job.exit_code(failed=3000, verify_mismatched=2), "partial")
        body = self.render(parsed, self.night(status, "verify_mismatch", copied=100, failed=3000))
        assert "FAILED, and verification found objects missing" in self.headline(body)

    def test_a_stop_does_not_hide_a_permanent_non_backup(self, parsed):
        """A collision on a stopped night is still an object never backed up.

        The stopped branch used to sit above the condition branches, so
        during the seed — when every night is a stopped night — a refused
        collision would have been swallowed by "nothing needs doing".
        """
        code = job.exit_code(failed=0, verify_mismatched=0, stopped=True, collisions=1)
        body = self.render(parsed, self.night(self.status_of(code, "partial", stopped=True), "collisions"))
        assert "OBJECTS NOT BACKED UP" in self.headline(body)
        assert "asked to stop before it finished" in body, (
            "the stop vanished entirely instead of moving below the headline"
        )
        assert "nothing needs doing" not in body


class TestTheFallbackRecoversTheCountsToo:
    """The night the report route exists for is a night that copied a lot."""

    def render_with_report(self, parsed: dict, report: dict, log: str) -> str:
        import json
        import subprocess
        import tempfile
        from pathlib import Path as P

        steps = parsed["jobs"]["mirror"]["steps"]
        script = next(
            s["run"] for s in steps
            if s.get("name", "").startswith("Write the run summary")
        ).replace("${{ steps.run.outcome }}", "$OUTCOME")
        with tempfile.TemporaryDirectory() as tmp:
            (P(tmp) / "mirror-output.txt").write_text(log)
            fake_bin = P(tmp) / "bin"
            fake_bin.mkdir()
            body_json = json.dumps(report, indent=2, sort_keys=True)
            (fake_bin / "ssh").write_text(
                "#!/bin/sh\n"
                'case "$*" in\n'
                '  *actions-run.started*) echo "1-1 1700000000" ;;\n'
                "  *find*) echo /var/lib/x/_runs/r.json ;;\n"
                f"  *cat*)  cat <<'JSON'\n{body_json}\nJSON\n;;\n"
                "esac\n"
            )
            (fake_bin / "ssh").chmod(0o755)
            out = P(tmp) / "summary.md"
            result = subprocess.run(
                ["bash", "-e", "-c", script],
                env={
                    "PATH": f"{fake_bin}:/usr/bin:/bin", "RUNNER_TEMP": tmp,
                    "ENV_NAME": "prod", "OUTCOME": "failure",
                    "DEPLOY_USER": "deploy", "DEPLOY_HOST": "host",
                    "RUN_TAG": "1-1", "STATE_DIR": "/var/lib/x",
                    "GITHUB_STEP_SUMMARY": str(out), "LC_ALL": "C",
                },
                capture_output=True, text=True,
            )
            assert result.returncode == 0, result.stderr.strip()[:300]
            return out.read_text() if out.exists() else ""

    # A truncated log: the pipe died before the run printed anything final.
    TRUNCATED = "2026-08-31 02:20:00,1 INFO batch: 20000 object(s), 4 GiB\n"

    def test_a_timed_out_seed_night_reports_what_it_copied(self, parsed):
        """412,000 objects copied, and the summary said it copied nothing.

        The fallback recovered `status` and `flags` and left the counts
        empty, so the stopped branch took its "copied nothing" path and
        diagnosed a stall in the manifest read.
        """
        body = self.render_with_report(
            parsed,
            {
                "status": "stopped", "flags": [], "outcome": "partial",
                "stats": {
                    "copied": 412000, "already_current": 0,
                    "verify_checked": 0, "verify_unverified": 0,
                },
            },
            self.TRUNCATED,
        )
        assert "stopped, progress kept" in body
        assert "412,000 images copied" in body, body[:400]
        assert "stopped before copying anything" not in body, (
            "a night that copied 412,000 objects says it copied none"
        )

    def test_a_clean_night_recovered_from_the_report_is_not_called_malformed(
        self, parsed
    ):
        """"(no counts in the log — check it)" means the log is broken."""
        body = self.render_with_report(
            parsed,
            {
                "status": "ok", "flags": [], "outcome": "ok",
                "stats": {
                    "copied": 4211, "already_current": 0,
                    "verify_checked": 50, "verify_unverified": 0,
                },
            },
            self.TRUNCATED,
        )
        assert "4,211 images copied" in body, body[:400]
        assert "50 verified" in body
        assert "no counts in the log" not in body

    def search_args(self, parsed: dict, marker: str):
        """Run the step with a stand-in `find`; return the args it was given.

        Observed rather than executed for real: `-printf` is GNU-only, so on
        a BSD/macOS box the real command returns nothing whatever the flags
        say and an execution test cannot tell a mutation apart. What the step
        BUILDS is the property that matters, and it holds anywhere.
        """
        import subprocess
        import tempfile
        from pathlib import Path as P

        steps = parsed["jobs"]["mirror"]["steps"]
        script = next(
            s["run"] for s in steps
            if s.get("name", "").startswith("Write the run summary")
        ).replace("${{ steps.run.outcome }}", "$OUTCOME")
        with tempfile.TemporaryDirectory() as tmp:
            root = P(tmp)
            (root / "mirror-output.txt").write_text(self.TRUNCATED)
            state = root / "state"
            (state / "_runs").mkdir(parents=True)
            (state / "actions-run.started").write_text(marker + chr(10))
            fake_bin = root / "bin"
            fake_bin.mkdir()
            seen = root / "find-args.txt"
            (fake_bin / "ssh").write_text(chr(10).join([
                "#!/bin/sh",
                "while [ $# -gt 0 ]; do",
                '  case "$1" in *@*) shift; break ;; *) shift ;; esac',
                "done",
                'exec /bin/sh -c "$*"',
                "",
            ]))
            (fake_bin / "find").write_text(chr(10).join([
                "#!/bin/sh",
                f'printf "%s\\n" "$*" > {seen}',
                "",
            ]))
            for name in ("ssh", "find"):
                (fake_bin / name).chmod(0o755)
            out = root / "summary.md"
            result = subprocess.run(
                ["bash", "-e", "-c", script],
                env={
                    "PATH": f"{fake_bin}:/usr/bin:/bin", "RUNNER_TEMP": str(root),
                    "ENV_NAME": "prod", "OUTCOME": "failure",
                    "DEPLOY_USER": "deploy", "DEPLOY_HOST": "host",
                    "RUN_TAG": "1-1", "STATE_DIR": str(state),
                    "GITHUB_STEP_SUMMARY": str(out), "LC_ALL": "C",
                },
                capture_output=True, text=True,
            )
            assert result.returncode == 0, result.stderr.strip()[:300]
            return (seen.read_text() if seen.exists() else None), str(state)

    def test_the_search_is_scoped_to_reports_written_after_this_run_began(
        self, parsed
    ):
        """The seed writes into the same `_runs/` directory.

        The marker names this job, so the tag check passes — but without
        `-newermt` the newest report there is the seed's, and the seed's
        verdict becomes this night's headline. That is the exact failure the
        run-scoping was added to fix, and only the tag half had a test.
        """
        args, state = self.search_args(parsed, "1-1 1700000000")
        assert args is not None, "the fallback never searched for a report"
        assert "-newermt @1700000000" in args, (
            f"the search is not scoped to this run's start: {args}"
        )
        assert f"{state}/_runs" in args

    def test_the_search_does_not_run_when_the_marker_names_another_job(
        self, parsed
    ):
        args, _ = self.search_args(parsed, "999-1 1700000000")
        assert args is None, "it searched for a report belonging to another job"

    def test_a_marker_without_a_usable_time_searches_for_nothing(self, parsed):
        """A malformed marker must not produce an unscoped search.

        Both guards matter and they fail differently: with no numeric check
        the step builds `-newermt @<garbage>`, and with no emptiness check it
        builds a bare `-newermt @`. Either way the scoping is gone, which is
        the whole point of the flag.
        """
        for marker in ("1-1 garbage", "1-1", "1-1 17e9", "1-1 -5"):
            args, _ = self.search_args(parsed, marker)
            assert args is None, (
                f"marker {marker!r} produced a search anyway: {args}"
            )

    def test_a_recovered_night_does_not_promise_nothing_needs_doing(self, parsed):
        """The report is written BEFORE the ledger upload runs.

        So the two ledger flags can never be in it — the workflow's own
        comment says so. The compensation it claimed was the exit code, but
        a cancelled job never receives one: the pipe is dead, which is the
        whole premise of this route. Meanwhile the run does reach
        `publish_ledger`, so the Box copy of the ledger really can be stale
        while the summary says nothing is wrong.
        """
        body = self.render_with_report(
            parsed,
            {"status": "stopped", "flags": [], "outcome": "partial",
             "stats": {"copied": 412000, "already_current": 0,
                       "verify_checked": 0, "verify_unverified": 0}},
            self.TRUNCATED,
        )
        assert "stopped, progress kept" in body
        assert "Nothing needs doing" not in body, (
            "a night whose ledger upload may have failed says nothing is wrong"
        )
        assert "Check the job log for the ledger upload" in body

    def test_a_night_read_from_the_log_still_says_nothing_needs_doing(self, parsed):
        """The other direction: the log route carries the ledger flags, so a
        clean stop there really does need nothing."""
        log = (
            "2026-08-31 02:20:00,1 INFO done — copied 4211, failed 0, "
            "already current 0, skipped 0\n"
            f"2026-08-31 02:45:00,1 INFO {job.STATUS_KEY}=stopped\n"
            f"2026-08-31 02:45:00,1 INFO {job.FLAGS_KEY}=\n"
        )
        body = self.render_with_report(
            parsed,
            {"status": "stopped", "flags": [], "outcome": "partial", "stats": {}},
            log,
        )
        assert "Nothing needs doing" in body

    def test_the_log_still_wins_when_it_has_the_counts(self, parsed):
        """The fallback fills gaps; it must not overwrite a complete log."""
        log = (
            "2026-08-31 02:20:00,1 INFO done — copied 7, failed 0, "
            "already current 0, skipped 0\n"
        )
        body = self.render_with_report(
            parsed,
            {"status": "ok", "flags": [], "outcome": "ok",
             "stats": {"copied": 999999, "already_current": 0,
                       "verify_checked": 0, "verify_unverified": 0}},
            log,
        )
        assert "7 images copied" in body, body[:300]
        assert "999,999" not in body


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


class TestACollisionIsNeverSwallowedByTheHeadline:
    """It was the only flag whose notice lived inside a branch.

    Six of the seven flags render through their own `if has_flag …` after the
    chain; `collisions` had a headline branch plus a note nested inside the
    verification branch. A night that failed copies AND found a mismatch took
    an earlier branch than either, so the word "collision" appeared nowhere —
    on a night where an object is permanently not backed up.
    """

    def render(self, parsed: dict, status: str, *flags: str) -> str:
        import subprocess
        import tempfile
        from pathlib import Path as P

        for flag in flags:
            assert flag in job.FLAG_VALUES, flag
        assert status in job.STATUS_VALUES, status
        log = (
            "2026-08-31 02:20:00,1 INFO done — copied 10, failed 3, "
            "already current 0, skipped 0\n"
            f"2026-08-31 02:45:00,1 INFO {job.STATUS_KEY}={status}\n"
            f"2026-08-31 02:45:00,1 INFO {job.FLAGS_KEY}={','.join(flags)}\n"
        )
        steps = parsed["jobs"]["mirror"]["steps"]
        script = next(
            s["run"] for s in steps
            if s.get("name", "").startswith("Write the run summary")
        ).replace("${{ steps.run.outcome }}", "$OUTCOME")
        with tempfile.TemporaryDirectory() as tmp:
            (P(tmp) / "mirror-output.txt").write_text(log)
            out = P(tmp) / "summary.md"
            r = subprocess.run(
                ["bash", "-e", "-c", script],
                env={
                    "PATH": "/usr/bin:/bin", "RUNNER_TEMP": tmp, "ENV_NAME": "prod",
                    "OUTCOME": "failure", "GITHUB_STEP_SUMMARY": str(out),
                    "LC_ALL": "C",
                },
                capture_output=True, text=True,
            )
            assert r.returncode == 0, r.stderr.strip()[:200]
            return out.read_text()

    def test_a_failed_night_that_also_mismatched_still_names_the_collision(
        self, parsed
    ):
        """All three conditions in one pass, and all three are reachable."""
        body = self.render(parsed, "failed", "collisions", "verify_mismatch")
        assert "collision" in body, (
            "an object permanently not backed up is mentioned nowhere"
        )
        assert "entangled" in body, "the ordering advice went with it"

    def test_it_is_not_repeated_under_its_own_headline(self, parsed):
        body = self.render(parsed, "partial", "collisions")
        assert "OBJECTS NOT BACKED UP" in body
        assert body.count("collision was also refused") == 0

    def test_a_collision_alone_keeps_its_own_headline(self, parsed):
        """The additive note is only for when something outranked it.

        Every branch above the collision one is a stop, a skip, or a
        verification mismatch, so a collision on its own always headlines.
        """
        body = self.render(parsed, "failed", "collisions")
        assert "OBJECTS NOT BACKED UP" in body
        assert "collision was also refused" not in body


class TestADryRunIsNotDescribedAsARealOne:
    """A dry run copies nothing, records no run and writes no run report.

    Every condition branch and every additive notice points at `_runs/` on
    Box. `report_dry_run` returns before `ledger.start_run` and before
    `publish_report`, so that file never exists — and this is step one of the
    pre-seed checklist and the first thing run on a rebuilt host.
    """

    DRY = (
        "2026-08-31 02:20:00,1 INFO dry run — would copy 5, 400 already "
        "current, 1 skipped; nothing was copied\n"
    )

    def render(self, parsed: dict, *flags: str, status: str = "partial") -> str:
        import subprocess
        import tempfile
        from pathlib import Path as P

        for flag in flags:
            assert flag in job.FLAG_VALUES, flag
        log = self.DRY + (
            f"2026-08-31 02:45:00,1 INFO {job.STATUS_KEY}={status}\n"
            f"2026-08-31 02:45:00,1 INFO {job.FLAGS_KEY}={','.join(flags)}\n"
        )
        steps = parsed["jobs"]["mirror"]["steps"]
        script = next(
            s["run"] for s in steps
            if s.get("name", "").startswith("Write the run summary")
        ).replace("${{ steps.run.outcome }}", "$OUTCOME")
        with tempfile.TemporaryDirectory() as tmp:
            (P(tmp) / "mirror-output.txt").write_text(log)
            out = P(tmp) / "summary.md"
            r = subprocess.run(
                ["bash", "-e", "-c", script],
                env={
                    "PATH": "/usr/bin:/bin", "RUNNER_TEMP": tmp, "ENV_NAME": "prod",
                    "OUTCOME": "success", "GITHUB_STEP_SUMMARY": str(out),
                    "LC_ALL": "C",
                },
                capture_output=True, text=True,
            )
            assert r.returncode == 0, r.stderr.strip()[:200]
            return out.read_text()

    def test_it_says_it_was_a_dry_run(self, parsed):
        body = self.render(parsed)
        assert "dry run" in body
        assert "would copy 5" in body

    def test_a_refused_name_does_not_point_at_a_report_that_does_not_exist(
        self, parsed
    ):
        body = self.render(parsed, "skipped_names")
        assert "_runs/" not in body, (
            "the operator is sent to a run report a dry run never writes"
        )
        assert "skipping" in body, "nothing tells them where the names are"

    def test_a_collision_does_not_claim_the_run_was_recorded(self, parsed):
        """It refused to overwrite nothing — it copied nothing at all."""
        body = self.render(parsed, "collisions")
        assert "recorded **partial**" not in body
        assert "refused to overwrite" not in body
        assert "would copy 5" in body, "its counts vanished"

    def test_a_vanished_source_does_not_point_at_a_report_either(self, parsed):
        body = self.render(parsed, "source_gone")
        assert "_runs/" not in body
