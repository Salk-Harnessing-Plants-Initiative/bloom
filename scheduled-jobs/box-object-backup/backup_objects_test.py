"""Behavioural tests for copier.py and the batched planning in backup_objects.py.

The rclone daemon is replaced by a fake that records every copy request, so
these exercise the parts that decide *what* gets sent and *what happens when
Box says no* — retry/backoff, ledger durability, failure accounting — without
a daemon, MinIO, or a Box account.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import backup_objects as job  # noqa: E402
from runlock import SKIP_MARKER, LockHeld, RunLock  # noqa: E402
import copier  # noqa: E402
import stopping  # noqa: E402
from backup_lib import CopyPlan, StorageObject, build_plan  # noqa: E402
from ledger import Ledger  # noqa: E402
from rclone_rc import MinioSource, RcloneError  # noqa: E402

VERSION = "0f8b1c2a-4d5e-4f60-9a1b-2c3d4e5f6a7b"
MINIO = MinioSource("http://supabase-minio:9000", "root", "secret", "bloom-storage")
BOX_FS = "box:"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Retries back off for real seconds; the tests should not wait them out."""
    monkeypatch.setattr(copier.time, "sleep", lambda _seconds: None)


def obj(name: str = "exp-42/frame.png", version: str = VERSION, size: int = 100):
    return StorageObject("images", name, version, size, "2026-08-01T12:00:00+00")


class FakeRclone:
    """Stands in for RcloneRC. `failures` maps a src key to errors to raise."""

    def __init__(self, failures: dict[str, list[RcloneError]] | None = None):
        self.calls: list[tuple[str, str, str, str]] = []
        self.failures = failures or {}
        self.stats_by_path: dict[str, dict] = {}
        self.stat_calls: list[str] = []

    def copy_file(self, src_fs, src_remote, dst_fs, dst_remote):
        self.calls.append((src_fs, src_remote, dst_fs, dst_remote))
        queued = self.failures.get(src_remote)
        if queued:
            raise queued.pop(0)

    def stat(self, fs, remote):
        self.stat_calls.append(remote)
        return self.stats_by_path.get(remote)


@pytest.fixture
def ledger(tmp_path):
    # Through Ledger.open(), NOT a hand-built connection. The fixture used to
    # re-create what open() does — including check_same_thread=False, which the
    # copy workers need — and that made the real constructor untested: deleting
    # the flag from production left the whole suite green while a real seed
    # would die on its first concurrent copy.
    led = Ledger.open(str(tmp_path / "ledger.db"))
    yield led
    led.close()


def run_copy(client, objects, ledger, box_root="Bloom-Backups/prod", workers=2):
    plan = build_plan(objects, ledger.copied_versions())
    return copier.copy_all(client, plan, MINIO, BOX_FS, box_root, ledger, workers)


# ---------- what actually gets sent ----------

def test_copy_sends_the_versioned_minio_key_as_the_source(ledger):
    client = FakeRclone()
    run_copy(client, [obj()], ledger)
    assert client.calls[0][1] == f"images/exp-42/frame.png/{VERSION}"


def test_copy_sends_the_unversioned_storage_path_as_the_destination(ledger):
    client = FakeRclone()
    run_copy(client, [obj()], ledger)
    assert client.calls[0][3] == "Bloom-Backups/prod/images/exp-42/frame.png"


def test_copy_destination_keeps_the_extension(ledger):
    client = FakeRclone()
    run_copy(client, [obj()], ledger)
    assert client.calls[0][3].endswith(".png")


def test_copy_reads_from_the_minio_connection_string(ledger):
    client = FakeRclone()
    run_copy(client, [obj()], ledger)
    assert client.calls[0][0].startswith(":s3,")


def test_copy_writes_to_the_box_remote(ledger):
    client = FakeRclone()
    run_copy(client, [obj()], ledger)
    assert client.calls[0][2] == BOX_FS


def test_every_planned_object_is_copied_once(ledger):
    client = FakeRclone()
    objects = [obj(name=f"a/{i}.png") for i in range(5)]
    copied, failed = run_copy(client, objects, ledger)
    assert (copied, failed, len(client.calls)) == (5, 0, 5)


# ---------- ledger durability ----------

def test_a_successful_copy_is_recorded(ledger):
    run_copy(FakeRclone(), [obj()], ledger)
    assert ledger.copied_versions()[("images", "exp-42/frame.png")].version == VERSION


def test_a_failed_copy_is_not_recorded(ledger):
    client = FakeRclone({f"images/exp-42/frame.png/{VERSION}": [RcloneError("nope")]})
    copied, failed = run_copy(client, [obj()], ledger)
    assert (copied, failed) == (0, 1) and ledger.copied_versions() == {}


def test_a_second_run_skips_what_the_first_copied(ledger):
    objects = [obj(name=f"a/{i}.png") for i in range(3)]
    run_copy(FakeRclone(), objects, ledger)
    second = FakeRclone()
    copied, _ = run_copy(second, objects, ledger)
    assert (copied, second.calls) == (0, [])


def test_a_second_run_recopies_an_object_whose_version_changed(ledger):
    run_copy(FakeRclone(), [obj()], ledger)
    second = FakeRclone()
    run_copy(second, [obj(version="99999999-0000-0000-0000-000000000000")], ledger)
    assert len(second.calls) == 1


def test_many_workers_write_the_ledger_concurrently(ledger):
    """Regression: SQLite rejects cross-thread use unless the ledger allows it."""
    objects = [obj(name=f"a/{i}.png") for i in range(60)]
    copied, failed = run_copy(FakeRclone(), objects, ledger, workers=8)
    assert (copied, failed) == (60, 0)
    assert len(ledger.copied_versions()) == 60


def test_the_survivors_of_a_partial_run_are_still_recorded(ledger):
    failing = obj(name="a/bad.png")
    client = FakeRclone(
        {f"images/a/bad.png/{VERSION}": [RcloneError("nope")] * copier.MAX_ATTEMPTS}
    )
    copied, failed = run_copy(client, [obj(name="a/good.png"), failing], ledger)
    assert (copied, failed) == (1, 1)
    assert ("images", "a/good.png") in ledger.copied_versions()


# ---------- retry behaviour ----------

def test_a_throttled_copy_is_retried_and_succeeds(ledger):
    key = f"images/exp-42/frame.png/{VERSION}"
    client = FakeRclone({key: [RcloneError("429 rate_limit", retryable=True)]})
    copied, failed = run_copy(client, [obj()], ledger)
    assert (copied, failed, len(client.calls)) == (1, 0, 2)


def test_retries_stop_at_the_attempt_cap(ledger):
    key = f"images/exp-42/frame.png/{VERSION}"
    client = FakeRclone(
        {key: [RcloneError("429", retryable=True) for _ in range(copier.MAX_ATTEMPTS + 2)]}
    )
    copied, failed = run_copy(client, [obj()], ledger)
    assert (copied, failed, len(client.calls)) == (0, 1, copier.MAX_ATTEMPTS)


def test_a_permanent_error_is_not_retried(ledger):
    key = f"images/exp-42/frame.png/{VERSION}"
    client = FakeRclone({key: [RcloneError("404 not found", retryable=False)]})
    copied, failed = run_copy(client, [obj()], ledger)
    assert (copied, failed, len(client.calls)) == (0, 1, 1)


def test_backoff_grows_between_attempts(monkeypatch, ledger):
    delays: list[int] = []
    monkeypatch.setattr(copier.time, "sleep", lambda seconds: delays.append(seconds))
    key = f"images/exp-42/frame.png/{VERSION}"
    client = FakeRclone({key: [RcloneError("429", retryable=True)] * 2})
    run_copy(client, [obj()], ledger)
    assert delays == [copier.RETRY_BASE_SECONDS, copier.RETRY_BASE_SECONDS * 2]


def test_one_object_failing_does_not_abort_the_rest(ledger):
    objects = [obj(name=f"a/{i}.png") for i in range(4)]
    client = FakeRclone({f"images/a/2.png/{VERSION}": [RcloneError("boom")]})
    copied, failed = run_copy(client, objects, ledger)
    assert (copied, failed) == (3, 1)


# ---------- verification pass ----------

def make_plan(objects):
    return build_plan(objects, {})


# The number verify_sample RETURNS is what everything downstream acts on: exit
# code 4, the run being recorded `partial` rather than `ok` (which is what holds
# the watermark back), and the VERIFICATION FAILED headline in the job summary.
# Each of those three is tested on its own, and each is handed a number by hand
# — so nothing joined the function that produces the count to the things that
# consume it. Asserting only on caplog let `return 0`, and every one of the
# three `mismatched += 1` lines, be deleted with the suite green.


def test_verify_accepts_a_matching_size(caplog, ledger):
    caplog.set_level(logging.INFO)
    client = FakeRclone()
    client.stats_by_path["root/images/exp-42/frame.png"] = {"Size": 100}
    assert copier.verify_sample(client, make_plan([obj()]), BOX_FS, "root", 1) == 0
    assert "1 checked, 0 mismatched" in caplog.text


def test_verify_flags_a_missing_destination(caplog, ledger):
    found = copier.verify_sample(FakeRclone(), make_plan([obj()]), BOX_FS, "root", 1)
    assert found == 1, "an object absent from Box was not counted"
    assert "missing on Box" in caplog.text


def test_verify_flags_a_size_mismatch(caplog, ledger):
    client = FakeRclone()
    client.stats_by_path["root/images/exp-42/frame.png"] = {"Size": 7}
    found = copier.verify_sample(client, make_plan([obj()]), BOX_FS, "root", 1)
    assert found == 1, "a wrong-sized object on Box was not counted"
    assert "size mismatch" in caplog.text


def test_verify_counts_an_object_it_could_not_check(caplog, ledger):
    """Box refusing the question, rather than answering it.

    The likeliest of the three outcomes and the only one with no test: the 50
    stat calls fire straight after a night that may have pushed hundreds of
    thousands of objects, which is exactly when Box throttles. If this stopped
    counting, every check could fail while the run recorded `ok`, advanced the
    watermark, and reported that verification passed.
    """

    class StatRefuses(FakeRclone):
        def stat(self, fs, remote):
            raise RcloneError("429 too many requests", retryable=True)

    found = copier.verify_sample(StatRefuses(), make_plan([obj()]), BOX_FS, "root", 1)
    assert found == 1, "an object that could not be checked was not counted"
    assert "cannot stat" in caplog.text


def test_verify_counts_every_bad_object_not_just_the_first(ledger):
    """The count is a count, not a flag — the summary reports `N of M`."""
    objects = [obj(name=f"exp-42/{n}.png") for n in range(3)]
    assert copier.verify_sample(FakeRclone(), make_plan(objects), BOX_FS, "root", 3) == 3


def test_verify_checks_the_requested_number_of_objects(ledger):
    client = FakeRclone()
    objects = [obj(name=f"a/{i}.png") for i in range(100)]
    copier.verify_sample(client, make_plan(objects), BOX_FS, "", 4)
    assert len(client.stat_calls) == 4


def test_verify_spreads_its_sample_across_the_plan(ledger):
    client = FakeRclone()
    objects = [obj(name=f"a/{i:03d}.png") for i in range(100)]
    copier.verify_sample(client, make_plan(objects), BOX_FS, "", 4)
    assert client.stat_calls == [
        "images/a/000.png", "images/a/025.png", "images/a/050.png", "images/a/075.png",
    ]


def test_verify_is_repeatable_so_a_mismatch_stays_reproducible(ledger):
    objects = [obj(name=f"a/{i:03d}.png") for i in range(50)]
    first, second = FakeRclone(), FakeRclone()
    copier.verify_sample(first, make_plan(objects), BOX_FS, "", 5)
    copier.verify_sample(second, make_plan(objects), BOX_FS, "", 5)
    assert first.stat_calls == second.stat_calls


def test_verify_of_an_empty_plan_checks_nothing(caplog, ledger):
    caplog.set_level(logging.INFO)
    copier.verify_sample(FakeRclone(), CopyPlan((), (), 0), BOX_FS, "", 5)
    assert "0 checked" in caplog.text


# ---------- batched planning over a manifest file ----------

def write_manifest(tmp_path, objects) -> Path:
    path = tmp_path / "manifest.tsv"
    path.write_text(
        "".join(
            f"{o.bucket_id}\t{o.name}\t{o.version}\t{o.size}\t{o.updated_at}\n"
            for o in objects
        )
    )
    return path


@pytest.fixture
def small_batches(monkeypatch):
    monkeypatch.setattr(job, "BATCH_SIZE", 3)


def test_plan_batches_covers_every_object(tmp_path, ledger, small_batches):
    objects = [obj(name=f"a/{i}.png") for i in range(10)]
    plans = list(job.plan_batches(write_manifest(tmp_path, objects), ledger, None))
    assert sum(len(p.copies) for p in plans) == 10


def test_plan_batches_splits_at_the_batch_size(tmp_path, ledger, small_batches):
    objects = [obj(name=f"a/{i}.png") for i in range(7)]
    plans = list(job.plan_batches(write_manifest(tmp_path, objects), ledger, None))
    assert [len(p.copies) for p in plans] == [3, 3, 1]


def test_plan_batches_respects_a_limit_spanning_batches(tmp_path, ledger, small_batches):
    objects = [obj(name=f"a/{i}.png") for i in range(10)]
    plans = list(job.plan_batches(write_manifest(tmp_path, objects), ledger, 5))
    assert sum(len(p.copies) for p in plans) == 5


def test_plan_batches_stops_reading_once_the_limit_is_met(tmp_path, ledger, small_batches):
    objects = [obj(name=f"a/{i}.png") for i in range(30)]
    plans = list(job.plan_batches(write_manifest(tmp_path, objects), ledger, 3))
    assert len(plans) == 1


def test_plan_batches_excludes_what_the_ledger_already_has(tmp_path, ledger, small_batches):
    objects = [obj(name=f"a/{i}.png") for i in range(6)]
    for done in objects[:4]:
        ledger.mark_copied(done)
    plans = list(job.plan_batches(write_manifest(tmp_path, objects), ledger, None))
    assert sum(len(p.copies) for p in plans) == 2
    assert sum(p.already_current for p in plans) == 4


def test_plan_batches_separates_unsafe_objects(tmp_path, ledger, small_batches):
    objects = [obj(name="a/ok.png"), obj(name="a/bad:name.png")]
    plans = list(job.plan_batches(write_manifest(tmp_path, objects), ledger, None))
    assert sum(len(p.skipped) for p in plans) == 1


def test_plan_batches_of_an_empty_manifest_yields_nothing(tmp_path, ledger):
    assert list(job.plan_batches(write_manifest(tmp_path, []), ledger, None)) == []


# ---------- credential handling ----------

def test_a_failure_is_logged_with_the_object_that_caused_it(caplog, ledger):
    # The copier logs whatever the error says. It does NOT redact — that
    # happens where the error is built, in RcloneRC.call, which is tested in
    # backup_lib_test.py. This asserts only what this layer is responsible for.
    key = f"images/exp-42/frame.png/{VERSION}"
    client = FakeRclone({key: [RcloneError("boom")]})
    run_copy(client, [obj()], ledger)
    assert "images/exp-42/frame.png" in caplog.text
    assert "boom" in caplog.text


class TestOutcomeProtectsTheWatermark:
    """A chunked seed must not poison the `since` watermark.

    `last_successful_run()` reads the newest run with outcome='ok'. A run cut
    short by --limit has not seen the whole table, so recording it clean would
    make the next run filter on its start time and skip every object the limit
    left behind — silently, and forever.
    """


    def test_a_complete_unlimited_run_is_clean(self):
        assert job.run_outcome(crashed=False, failed=0, copied=8_000_000, limit=None) == "ok"

    def test_a_run_that_hit_its_limit_is_partial(self):
        assert job.run_outcome(crashed=False, failed=0, copied=500_000, limit=500_000) == "partial"

    def test_a_limited_run_that_finished_early_is_clean(self):
        # Fewer copies than the cap means the table ran out first, not the
        # limit — that run really did see everything.
        assert job.run_outcome(crashed=False, failed=0, copied=1_200, limit=500_000) == "ok"

    def test_one_object_short_of_the_limit_still_saw_the_whole_table(self):
        # The boundary itself: `copied >= limit` means the run may have been
        # cut off, one below means it ran out of work first. Only far-from-the
        # -edge values were pinned, which hold either way.
        assert job.run_outcome(
            crashed=False, failed=0, copied=499_999, limit=500_000
        ) == "ok"

    def test_failures_still_mark_a_run_partial(self):
        assert job.run_outcome(crashed=False, failed=3, copied=10, limit=None) == "partial"

    def test_a_crash_outranks_everything(self):
        assert job.run_outcome(crashed=True, failed=0, copied=500_000, limit=500_000) == "error"


class TestVerificationCanFailARun:
    """A check whose verdict is discarded is not a check.

    verify_sample computed a mismatch count and the caller threw it away, so a
    run where every sampled object was missing from Box still exited 0, recorded
    'ok', and advanced the watermark past the objects it had just proved were
    absent.
    """

    def test_a_mismatch_stops_the_run_being_a_watermark(self):
        assert job.run_outcome(
            crashed=False, failed=0, copied=100, limit=None, verify_mismatched=1
        ) == "partial"

    def test_a_clean_verification_leaves_the_run_ok(self):
        assert job.run_outcome(
            crashed=False, failed=0, copied=100, limit=None, verify_mismatched=0
        ) == "ok"

    def test_a_crash_still_outranks_a_mismatch(self):
        assert job.run_outcome(
            crashed=True, failed=0, copied=100, limit=None, verify_mismatched=5
        ) == "error"

    def test_not_verifying_is_not_the_same_as_verifying_clean(self):
        # A run with --verify 0 reports verify_checked=0; that must not read as
        # "checked and fine" in the report.
        assert job.run_outcome(
            crashed=False, failed=0, copied=100, limit=None
        ) == "ok"


class TestVerifyReservoir:
    """The sample must cover the run, not its first few thousand objects, and
    must not change because the network was faster on one night."""

    def make(self, cap, n, order=None):
        r = copier.VerifyReservoir(cap)
        for i in (order if order is not None else range(n)):
            r.offer(f"obj-{i:06d}")
        return r

    def test_never_exceeds_its_cap(self):
        assert len(self.make(50, 10_000)) == 50

    def test_keeps_everything_when_under_cap(self):
        assert len(self.make(50, 20)) == 20

    def test_samples_beyond_the_head_of_the_run(self):
        # The old pool was `plan.copies[:n]` per batch — always the first
        # objects. With 10k offered and a cap of 50, a head-biased sample would
        # hold only obj-0000xx.
        r = self.make(50, 10_000)
        indices = [int(o.split("-")[1]) for o in r.items]
        assert max(indices) > 5_000, "sample never reaches the second half of the run"

    def test_covers_the_whole_range_not_one_cluster(self):
        r = self.make(200, 100_000)
        indices = sorted(int(o.split("-")[1]) for o in r.items)
        # Quartile coverage: a uniform sample lands in all four.
        quartiles = {i // 25_000 for i in indices}
        assert quartiles == {0, 1, 2, 3}, f"only covered quartiles {quartiles}"

    def test_is_reproducible_for_the_same_sequence(self):
        # A mismatch must stay findable on a re-run rather than vanishing.
        assert self.make(50, 10_000).items == self.make(50, 10_000).items

    def test_is_reproducible_even_when_the_order_changes(self):
        """The one that matters in production.

        `offer` is called by whichever copy worker finishes first, so the order
        follows Box's network timing and differs every run. Seeded reservoir
        sampling was reproducible for a fixed sequence and for nothing else —
        reproducible in this test file and in no real run.
        """
        import random

        shuffled = list(range(10_000))
        random.Random(7).shuffle(shuffled)
        in_order = self.make(50, 10_000)
        out_of_order = self.make(50, 10_000, order=shuffled)
        assert out_of_order.items == in_order.items, (
            "a different arrival order sampled different objects"
        )

    def test_the_sample_does_not_depend_on_the_process(self):
        """str hashing is salted per process; the sample must not be.

        Pinned to actual values so a change of hash shows up as a failing test
        rather than as verification quietly checking somewhere else.
        """
        # Confirmed identical under PYTHONHASHSEED 0, 1 and 12345.
        assert self.make(3, 1_000).items == [
            "obj-000815", "obj-000894", "obj-000343"
        ]

    def test_counts_everything_it_was_offered(self):
        assert self.make(50, 10_000).seen == 10_000

    def test_an_object_that_cannot_be_ordered_is_still_accepted(self):
        """Ties must never compare the objects themselves.

        StorageObject is a frozen dataclass with no ordering, so a tie that
        reached it would raise TypeError mid-run, inside a copy worker.
        """
        r = copier.VerifyReservoir(5)
        for _ in range(10):
            r.offer(obj(name="same/path.png"))
        assert len(r) == 5


class TestOnlyProductionCanBeMirrored:
    """Staging is never backed up, and must not be reachable by hand either.

    Both environments run on this one host and the state directory has no
    environment in it, so a staging run would open the same `ledger.db` and
    write the same `runs` table — which is the watermark. The two would then
    advance each other's timestamp, each skipping whatever the other had
    already covered, silently and in both directions. Removing the option from
    the workflow closes that from Actions; this closes it from a shell.
    """

    def test_staging_is_not_an_accepted_environment(self):
        with pytest.raises(SystemExit):
            job.parse_args(["--env", "staging", "--box-root", "x"])

    def test_production_still_is(self):
        assert job.parse_args(["--env", "prod", "--box-root", "x"]).env == "prod"


class TestBoxRootIsChecked:
    """An unset destination writes 8M objects to the top of the Box drive.

    --box-root defaults to "" and nothing validated it, so a manual seed over
    SSH without BACKUP_BOX_ROOT exported — the exact workflow the wiki
    describes — would mirror the whole deploy into the root of the account.
    """

    @staticmethod
    def args(box_root, env="prod"):
        import argparse
        return argparse.Namespace(box_root=box_root, env=env)

    def test_an_empty_root_is_refused(self):
        with pytest.raises(job.lib.BackupError, match="BACKUP_BOX_ROOT is empty"):
            job.check_box_root(self.args(""))

    def test_whitespace_and_slashes_do_not_count_as_a_root(self):
        for blank in ("   ", "/", "///", " / "):
            with pytest.raises(job.lib.BackupError, match="empty"):
                job.check_box_root(self.args(blank))

    def test_a_real_root_passes(self):
        job.check_box_root(self.args("Bloom-Backups/BloomV2-Data-Backup/prod/storage"))

    def test_the_error_names_the_variable_an_operator_must_set(self):
        with pytest.raises(job.lib.BackupError) as caught:
            job.check_box_root(self.args(""))
        assert "BACKUP_BOX_ROOT" in str(caught.value)
        assert "prod" in str(caught.value)


class TestWatermarkOrdering:
    """The watermark must not be able to skip past the enumeration window.

    `last_successful_run()` returns `runs.started_at`, which the next run uses
    as `updated_at > since`. If that timestamp is taken AFTER the manifest
    snapshot closed, objects written in between are below the next run's
    watermark and above this run's snapshot — enumerated by neither, forever.
    """

    def test_the_watermark_is_read_before_the_manifest(self, monkeypatch, tmp_path):
        """Ordering asserted by CALL ORDER, not by where a string sits.

        This read the source and compared index positions, which a comment
        naming the function satisfied — moving the real call below the snapshot
        while leaving `# previously: dock.database_now(` above it passed.
        """
        calls = []

        class FakeDock:
            DB_SERVICE = "db-prod"
            DockerError = job.dock.DockerError

            @staticmethod
            def project_name(env):
                return f"bloom_v2_{env}"

            @staticmethod
            def find_container(project, service):
                return "container"

            @staticmethod
            def database_now(container, user, database):
                calls.append("watermark")
                return "2026-08-31T02:17:03+00"

            @staticmethod
            def psql_query_to_file(container, sql, user, database, destination):
                calls.append("snapshot")
                destination.write_text("")
                return 0

        monkeypatch.setattr(job, "dock", FakeDock)
        args = job.parse_args([
            "--env", "prod", "--dry-run",
            "--state-dir", str(tmp_path),
            "--box-root", "Bloom-Backups/prod/storage",
            "--minio-bucket", "bloom-storage",
        ])
        job.run_locked(args, tmp_path)

        assert calls == ["watermark", "snapshot"], (
            "the watermark must be read before the snapshot; taken after, an "
            "object written during enumeration is below the next run's filter "
            "and above this run's snapshot, so nothing ever sees it again"
        )

    def test_the_watermark_recorded_is_the_one_the_database_gave(
        self, monkeypatch, tmp_path
    ):
        """The value must come from the DB, not from the host clock.

        `ledger.start_run()` with no argument stamps datetime.now() on the
        deploy host, while updated_at is written by Postgres. The old test was
        a bare substring check and passed with the real call reverted.
        """
        db_time = "2019-01-01T00:00:00+00"

        class FakeDock:
            DB_SERVICE = "db-prod"
            DockerError = job.dock.DockerError

            @staticmethod
            def project_name(env):
                return f"bloom_v2_{env}"

            @staticmethod
            def find_container(project, service):
                return "container"

            @staticmethod
            def database_now(container, user, database):
                return db_time

            @staticmethod
            def psql_query_to_file(container, sql, user, database, destination):
                destination.write_text("")
                return 0

        monkeypatch.setattr(job, "dock", FakeDock)
        # A dry run returns before start_run, so drive the ledger directly with
        # the value run_locked would hand it and prove it survives the round trip.
        led = Ledger.open(str(tmp_path / "ledger.db"))
        run_id = led.start_run(now=db_time)
        led.finish_run(run_id, "ok", {})
        assert led.last_successful_run() == db_time
        assert not led.last_successful_run().startswith("202" + "6"), (
            "a 2019 stamp came back as today's date — the host clock won"
        )
        led.close()

    def test_the_ledger_accepts_an_explicit_timestamp(self, ledger):
        # The mechanism the above relies on.
        run_id = ledger.start_run(now="2026-08-31T02:17:03+00")
        ledger.finish_run(run_id, "ok", {})
        assert ledger.last_successful_run() == "2026-08-31T02:17:03+00"

    def test_a_partial_run_does_not_become_the_watermark(self, ledger):
        clean = ledger.start_run(now="2026-08-24T02:00:00+00")
        ledger.finish_run(clean, "ok", {})
        later = ledger.start_run(now="2026-08-31T02:00:00+00")
        ledger.finish_run(later, "partial", {})
        assert ledger.last_successful_run() == "2026-08-24T02:00:00+00"


class TestExitCodeReachesTheWorkflow:
    """A scheduled run's only route to a human is failing.

    Verification found objects missing from Box and the run still exited 0, so
    Actions showed a green tick and nobody was told. The mismatch lived only in
    a report on Box that someone had to think to open.
    """

    def test_a_clean_run_is_zero(self):
        assert job.exit_code(failed=0, verify_mismatched=0) == 0

    def test_failed_copies_are_one(self):
        assert job.exit_code(failed=3, verify_mismatched=0) == 1

    def test_a_verification_mismatch_is_not_success(self):
        assert job.exit_code(failed=0, verify_mismatched=1) != 0

    def test_a_verification_mismatch_is_told_apart_from_failed_copies(self):
        # Different kinds of wrong: one says copies errored, the other says the
        # copies claimed success and the mirror disagrees.
        assert job.exit_code(failed=0, verify_mismatched=1) == 4
        assert job.exit_code(failed=0, verify_mismatched=1) != job.exit_code(
            failed=1, verify_mismatched=0
        )

    def test_failed_copies_outrank_a_mismatch(self):
        assert job.exit_code(failed=2, verify_mismatched=5) == 1

    def test_the_documented_codes_match_what_is_returned(self):
        doc = job.__doc__
        for code in ("1 =", "2 =", "3 =", "4 =", "5 ="):
            assert code in doc, f"exit {code[0]} undocumented"

    def test_a_refused_collision_is_not_a_clean_run(self):
        """Otherwise the watermark advances past an object that is not backed
        up, and the next run does not even enumerate it — the one log line
        naming it becomes the last anyone ever hears of it."""
        assert job.run_outcome(
            crashed=False, failed=0, copied=10, limit=None, collisions=1
        ) == "partial"

    def test_no_collisions_is_still_clean(self):
        assert job.run_outcome(
            crashed=False, failed=0, copied=10, limit=None, collisions=0
        ) == "ok"

    def test_a_crash_still_outranks_a_collision(self):
        assert job.run_outcome(
            crashed=True, failed=0, copied=10, limit=None, collisions=1
        ) == "error"

    def test_a_refused_collision_has_its_own_code(self):
        """Not 4. Exit 4's remedy is deleting the ledger row so the object is
        copied again; doing that to a collision loser only re-refuses it. This
        one needs a rename in Supabase."""
        assert job.exit_code(failed=0, verify_mismatched=0, collisions=1) == 5

    def test_a_run_with_no_collisions_is_unaffected(self):
        assert job.exit_code(failed=0, verify_mismatched=0, collisions=0) == 0

    def test_a_failed_copy_still_outranks_a_collision(self):
        # 1 first: an object that errored may succeed on a re-run, which is a
        # different thing to do about it than renaming a file.
        assert job.exit_code(failed=1, verify_mismatched=0, collisions=1) == 1

    def test_a_verification_mismatch_still_outranks_a_collision(self):
        assert job.exit_code(failed=0, verify_mismatched=1, collisions=1) == 4

    def test_a_collision_outranks_a_stop(self):
        # A stop is expected and resumable; a collision needs a person.
        assert job.exit_code(
            failed=0, verify_mismatched=0, stopped=True, collisions=1
        ) == 5


class TestRunLockedWiresItsPartsTogether:
    """`run_locked` is where every fix in this PR lives, and it had no test.

    Each fix was covered in isolation — source_remote, check_box_root,
    preflight_source, the verify plumbing — while nothing checked that the run
    CALLS them. Every one could be deleted from the run path with the suite
    green: the tenant prefix dropped, the box-root guard removed, the preflight
    skipped, the verifier's verdict discarded.

    These drive the real function with fakes at the process boundaries only.
    """

    MANIFEST = (
        "images\texp-42/a.png\tv1\t100\t2026-08-31T00:00:00+00\n"
        "images\texp-42/b.png\tv2\t200\t2026-08-31T00:00:01+00\n"
    )

    @pytest.fixture
    def harness(self, monkeypatch, tmp_path):
        """Fakes for docker and rclone; everything between them is real."""
        state = {"copied": [], "stat_calls": [], "missing": set(), "daemon_stopped": False}

        class FakeDaemon:
            container = "c"
            url = "http://127.0.0.1:5572"
            user = "u"
            password = "p"

            def stop(self):
                state["daemon_stopped"] = True

        class FakeDock:
            DB_SERVICE = "db-prod"
            STATE_MOUNT = "/state"
            RC_CONTAINER_PREFIX = job.dock.RC_CONTAINER_PREFIX
            DockerError = job.dock.DockerError

            @staticmethod
            def project_name(env):
                return f"bloom_v2_{env}"

            @staticmethod
            def find_container(project, service):
                return "container"

            @staticmethod
            def find_network(project):
                return "supanet"

            @staticmethod
            def database_now(container, user, database):
                return "2026-08-31T02:17:03+00"

            @staticmethod
            def psql_query_to_file(container, sql, user, database, destination):
                destination.write_text(TestRunLockedWiresItsPartsTogether.MANIFEST)
                return 2

            @staticmethod
            def find_stale_daemons():
                return state.get("stale", [])

            @staticmethod
            def start_rc_daemon(**kwargs):
                return FakeDaemon()

        class FakeClient:
            def copy_file(self, src_fs, src_remote, dst_fs, dst_remote):
                state["copied"].append((src_fs, src_remote, dst_remote))

            def stat(self, fs, remote):
                # Sizes must match the manifest or real verification correctly
                # reports a mismatch — a.png is 100 bytes, b.png is 200.
                state["stat_calls"].append(remote)
                if remote in state["missing"]:
                    return None
                return {"Size": 200 if remote.endswith("b.png") else 100}

            def noop(self):
                return {}

            def version(self):
                return "fake"

        monkeypatch.setattr(job, "dock", FakeDock)
        monkeypatch.setattr(job, "wait_for_daemon", lambda daemon, attempts=30: FakeClient())
        monkeypatch.setattr(job, "require_rclone_config", lambda path, remote: None)
        monkeypatch.setenv("MINIO_ROOT_USER", "root")
        monkeypatch.setenv("MINIO_ROOT_PASSWORD", "secret")
        state["client"] = FakeClient()
        return state, tmp_path

    def args(self, tmp_path, **overrides):
        argv = [
            "--env", "prod",
            "--state-dir", str(tmp_path),
            "--box-root", "Bloom-Backups/BloomV2-Data-Backup/prod/storage",
            "--minio-bucket", "bloom-storage",
            "--minio-prefix", "storage-single-tenant",
        ]
        for flag, value in overrides.items():
            argv += [f"--{flag.replace('_', '-')}", str(value)]
        return job.parse_args(argv)

    def test_the_run_copies_from_the_tenant_prefixed_path(self, harness):
        # The prefix was dropped from the run and no test noticed: the shared
        # MinIO fixture was built with prefix="", so copy_all was only ever
        # exercised without one.
        state, tmp_path = harness
        assert job.run_locked(self.args(tmp_path), tmp_path) == 0
        assert state["copied"], "nothing was copied"
        for src_fs, src_remote, _ in state["copied"]:
            assert src_fs.endswith(":bloom-storage"), src_fs
            assert src_remote.startswith("storage-single-tenant/images/"), src_remote

    def test_the_run_writes_to_the_configured_box_root(self, harness):
        state, tmp_path = harness
        job.run_locked(self.args(tmp_path), tmp_path)
        for _, _, dst in state["copied"]:
            assert dst.startswith("Bloom-Backups/BloomV2-Data-Backup/prod/storage/")

    def test_an_empty_box_root_stops_the_run_before_anything_is_copied(self, harness):
        # check_box_root could be deleted from run_locked with the suite green.
        state, tmp_path = harness
        args = self.args(tmp_path)
        args.box_root = ""
        with pytest.raises(job.lib.BackupError, match="BACKUP_BOX_ROOT"):
            job.run_locked(args, tmp_path)
        assert state["copied"] == [], "copied despite an unset destination"

    def test_a_preflight_where_everything_misses_stops_the_run(self, harness):
        # preflight_source could be deleted from run_locked with the suite green.
        # EVERY sample must miss: that is what means the layout is wrong.
        state, tmp_path = harness
        state["missing"].update({
            "storage-single-tenant/images/exp-42/a.png/v1",
            "storage-single-tenant/images/exp-42/b.png/v2",
        })
        with pytest.raises(job.lib.BackupError, match="preflight failed"):
            job.run_locked(self.args(tmp_path), tmp_path)
        assert state["copied"] == [], "copied despite the source layout being wrong"

    def test_one_orphaned_row_does_not_reject_a_correct_configuration(self, harness):
        """The reason the preflight samples several objects rather than one.

        The manifest is ordered `bucket_id, updated_at`, so the first row is
        the oldest object in the first bucket — the one most likely to have
        lost its bytes years ago while the row survived. Probing only that made
        a single dead row fail every run, weekly, blaming the bucket and prefix
        settings when they were correct.
        """
        state, tmp_path = harness
        state["missing"].add("storage-single-tenant/images/exp-42/a.png/v1")
        assert job.run_locked(self.args(tmp_path), tmp_path) == 0
        assert state["copied"], "a single orphaned row stopped a correct run"

    def test_verification_runs_and_a_mismatch_reaches_the_exit_code(self, harness):
        # The verifier's verdict was discarded; the run exited 0 regardless.
        state, tmp_path = harness
        args = self.args(tmp_path, verify=2)

        original = job.verify_sample
        monkey = {"called": False}

        def spy(client, plan, box_fs, box_root, sample):
            monkey["called"] = True
            return 1  # one mismatch

        job.verify_sample = spy
        try:
            code = job.run_locked(args, tmp_path)
        finally:
            job.verify_sample = original

        assert monkey["called"], "verification never ran"
        assert code == 4, f"a mismatch must fail the run, got exit {code}"

    def test_a_clean_verification_leaves_the_run_successful(self, harness):
        state, tmp_path = harness
        assert job.run_locked(self.args(tmp_path, verify=2), tmp_path) == 0

    def test_a_leftover_container_stops_the_run_before_anything_is_copied(self, harness):
        # The check must be reachable from the run, not merely importable.
        state, tmp_path = harness
        state["stale"] = ["bloom-box-backup-rclone-dead (Up 3 days)"]
        with pytest.raises(job.lib.BackupError, match="earlier run"):
            job.run_locked(self.args(tmp_path), tmp_path)
        assert state["copied"] == [], "copied despite a stale daemon holding the port"

    def test_a_bucket_scoped_run_does_not_record_itself_as_clean(self, harness):
        # run_outcome knowing about --buckets is useless if the run never tells
        # it. Removing the argument from the call site left the suite green.
        state, tmp_path = harness
        args = self.args(tmp_path)
        args.buckets = "images"
        assert job.run_locked(args, tmp_path) == 0

        import sqlite3
        rows = sqlite3.connect(tmp_path / "ledger.db").execute(
            "SELECT outcome FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchall()
        assert rows and rows[0][0] == "partial", (
            "a bucket-scoped run recorded itself clean, so the watermark "
            "advances for every bucket it never enumerated"
        )

    def test_a_whole_table_run_still_records_clean(self, harness):
        state, tmp_path = harness
        assert job.run_locked(self.args(tmp_path), tmp_path) == 0
        import sqlite3
        rows = sqlite3.connect(tmp_path / "ledger.db").execute(
            "SELECT outcome FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchall()
        assert rows and rows[0][0] == "ok"

    def test_the_daemon_is_stopped_even_when_the_run_raises(self, harness):
        state, tmp_path = harness
        state["missing"].update({
            "storage-single-tenant/images/exp-42/a.png/v1",
            "storage-single-tenant/images/exp-42/b.png/v2",
        })
        with pytest.raises(job.lib.BackupError):
            job.run_locked(self.args(tmp_path), tmp_path)
        assert state["daemon_stopped"], "the rclone container was left running"


class TestStaleDaemonStopsTheRun:
    """A leftover container is refused, not removed.

    Removing one is destructive, and this job should not do that on its own
    initiative. What it owes the operator is a message that names the container
    and the command — rather than docker's `port is already allocated`, which
    says nothing about a run three nights ago.
    """

    def test_a_leftover_refuses_the_run(self, monkeypatch):
        monkeypatch.setattr(
            job.dock, "find_stale_daemons",
            lambda: ["bloom-box-backup-rclone-a1b2 (Up 3 days)"],
        )
        with pytest.raises(job.lib.BackupError) as caught:
            job.check_no_stale_daemon()
        message = str(caught.value)
        assert "bloom-box-backup-rclone-a1b2" in message, "the container is not named"
        assert "docker rm" in message, "no command to act on"

    def test_a_clean_host_passes(self, monkeypatch):
        monkeypatch.setattr(job.dock, "find_stale_daemons", lambda: [])
        job.check_no_stale_daemon()

    def test_the_message_says_why_it_is_safe_to_remove(self, monkeypatch):
        # The operator's first worry is "is a backup using this?" — the run
        # lock is already held here, so nothing else can be.
        monkeypatch.setattr(job.dock, "find_stale_daemons", lambda: ["x (Up 1 day)"])
        with pytest.raises(job.lib.BackupError) as caught:
            job.check_no_stale_daemon()
        assert "lock" in str(caught.value)

    def test_the_check_runs_before_the_daemon_is_started(self):
        # Order is the property: checking after `docker run` has already failed
        # is no better than the error it replaces.
        source = (Path(__file__).parent / "backup_objects.py").read_text()
        executable = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        )
        assert executable.index("check_no_stale_daemon()") < executable.index(
            "dock.start_rc_daemon("
        )


class TestBucketScopedRunsCannotBecomeTheWatermark:
    """`--buckets` narrows what a run enumerates; the watermark does not narrow
    with it.

    A run scoped to one bucket finishing clean was recorded `ok`, so
    `last_successful_run()` returned its start time and the NEXT run filtered
    `updated_at > <that>` across ALL buckets. Every object in the buckets it
    never looked at, older than that moment, was never enumerated again —
    silently, with the run reporting success.
    """

    def test_a_bucket_scoped_run_is_partial(self):
        assert job.run_outcome(
            crashed=False, failed=0, copied=100, limit=None, bucket_scoped=True
        ) == "partial"

    def test_a_whole_table_run_is_still_ok(self):
        assert job.run_outcome(
            crashed=False, failed=0, copied=100, limit=None, bucket_scoped=False
        ) == "ok"

    def test_the_wiki_smoke_test_shape_is_partial(self):
        # --buckets images --limit 20: partial for two independent reasons.
        assert job.run_outcome(
            crashed=False, failed=0, copied=20, limit=20, bucket_scoped=True
        ) == "partial"

    def test_scoping_alone_is_enough_without_a_limit(self):
        # The dangerous form: --buckets with no --limit, which used to be ok.
        assert job.run_outcome(
            crashed=False, failed=0, copied=999, limit=None, bucket_scoped=True
        ) == "partial"

    def test_a_crash_still_outranks_it(self):
        assert job.run_outcome(
            crashed=True, failed=0, copied=1, limit=None, bucket_scoped=True
        ) == "error"


class TestAStoppedRunIsResumable:
    """Stopping must leave the run in a state the next one continues from.

    Two things have to be true, and they are separate: the run must not record
    itself as clean (or the watermark advances past objects it never reached),
    and it must report the interrupted exit code rather than the clean one.
    """

    def test_a_stopped_run_is_partial_not_ok(self):
        # Same reasoning as --limit and --buckets: it did not see the whole
        # table, so it cannot be what "everything up to here is backed up"
        # points at.
        assert job.run_outcome(
            crashed=False, failed=0, copied=500, limit=None, stopped=True
        ) == "partial"

    def test_a_run_that_finished_is_still_ok(self):
        assert job.run_outcome(
            crashed=False, failed=0, copied=500, limit=None, stopped=False
        ) == "ok"

    def test_a_stopped_run_exits_three(self):
        # 3 is already documented as "interrupted; progress is in the ledger
        # and the next run resumes".
        assert job.exit_code(failed=0, verify_mismatched=0, stopped=True) == 3

    def test_a_finished_run_still_exits_zero(self):
        assert job.exit_code(failed=0, verify_mismatched=0, stopped=False) == 0

    def test_real_failures_outrank_a_stop(self):
        # Objects that failed are worth more attention than the stop itself.
        assert job.exit_code(failed=2, verify_mismatched=0, stopped=True) == 1

    def test_a_verification_mismatch_outranks_a_stop(self):
        assert job.exit_code(failed=0, verify_mismatched=1, stopped=True) == 4

    def test_the_exit_code_is_documented(self):
        assert "3 = interrupted" in job.__doc__


class TestTheContainerIsAlwaysTornDown:
    """`daemon.stop()` is the one thing in the cleanup that must not be skipped.

    A container left holding the RC port makes every later night fail at
    check_no_stale_daemon until someone SSHes in. Reordering the cleanup so the
    ledger is closed before it is uploaded put daemon.stop() last, behind steps
    that can raise: publish_report catches only OSError and RcloneError, and
    the ledger writes can raise sqlite3.Error on a full disk — realistic on a
    state dir holding a 1.7 GB ledger and a 1 GB manifest.
    """

    @pytest.fixture
    def harness(self, monkeypatch, tmp_path):
        return TestRunLockedWiresItsPartsTogether().harness.__wrapped__(
            TestRunLockedWiresItsPartsTogether(), monkeypatch, tmp_path
        )

    def boom(self, *a, **kw):
        raise RuntimeError("disk full")

    def test_it_is_stopped_when_the_report_upload_raises(self, harness, monkeypatch):
        state, tmp_path = harness
        monkeypatch.setattr(job, "publish_report", self.boom)
        with pytest.raises(RuntimeError):
            job.run_locked(TestRunLockedWiresItsPartsTogether().args(tmp_path), tmp_path)
        assert state["daemon_stopped"], "the rclone container was stranded"

    def test_it_is_stopped_when_the_ledger_upload_raises(self, harness, monkeypatch):
        state, tmp_path = harness
        monkeypatch.setattr(job, "publish_ledger", self.boom)
        with pytest.raises(RuntimeError):
            job.run_locked(TestRunLockedWiresItsPartsTogether().args(tmp_path), tmp_path)
        assert state["daemon_stopped"], "the rclone container was stranded"

    def test_it_is_stopped_on_an_ordinary_clean_run(self, harness):
        state, tmp_path = harness
        assert job.run_locked(
            TestRunLockedWiresItsPartsTogether().args(tmp_path), tmp_path
        ) == 0
        assert state["daemon_stopped"]


class TestRunBackupTakesTheHostLock:
    """`run_backup` had no coverage at all — the lock could be deleted outright.

    It is one lock per host, and it is the only thing stopping the hand-run
    seed and the 02:17 nightly from writing to one SQLite ledger at the same
    time. Every other test in this file calls `run_locked`, which is past it.
    `RunLock` itself is well tested cross-process; nothing showed that
    `run_backup` uses it.

    The contract also crosses into the workflow: a run that cannot take the
    lock is NOT a failure. It exits 0 and prints a marker the job summary
    greps for, so a stood-down night reads as "skipped" rather than as a
    success — that difference is how a months-long gap would go unnoticed.
    """

    def args(self, tmp_path):
        return job.parse_args([
            "--env", "prod",
            "--state-dir", str(tmp_path),
            "--box-root", "Bloom-Backups/BloomV2-Data-Backup/prod/storage",
        ])

    def test_the_lock_is_actually_taken(self, tmp_path, monkeypatch):
        """While the run is going, nobody else can have it."""
        held = {}

        def while_running(args, state_dir):
            with pytest.raises(LockHeld):
                RunLock(state_dir).acquire()
            held["checked"] = True
            return 0

        monkeypatch.setattr(job, "run_locked", while_running)
        assert job.run_backup(self.args(tmp_path)) == 0
        assert held.get("checked"), "run_locked was never reached"

    def test_a_second_run_stands_down_without_failing(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setattr(
            job, "run_locked",
            lambda *a, **kw: pytest.fail("ran while another run held the lock"),
        )
        first = RunLock(tmp_path).acquire()
        try:
            assert job.run_backup(self.args(tmp_path)) == 0, (
                "a stood-down run failed the workflow instead of skipping"
            )
        finally:
            first.release()

    def test_a_stood_down_run_prints_the_marker_the_summary_greps(
        self, tmp_path, monkeypatch, caplog
    ):
        # The workflow greps this out of the log to tell a skipped night from a
        # successful one. Both exit 0, so the marker is the only difference.
        monkeypatch.setattr(job, "run_locked", lambda *a, **kw: 0)
        first = RunLock(tmp_path).acquire()
        try:
            job.run_backup(self.args(tmp_path))
        finally:
            first.release()
        assert SKIP_MARKER in caplog.text, "a skipped night looks like a clean one"

    def test_a_stood_down_run_names_who_is_holding_it(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setattr(job, "run_locked", lambda *a, **kw: 0)
        first = RunLock(tmp_path).acquire()
        try:
            job.run_backup(self.args(tmp_path))
        finally:
            first.release()
        assert "held by" in caplog.text
        assert str(os.getpid()) in caplog.text, "does not say which process has it"

    def test_the_lock_is_released_when_the_run_finishes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(job, "run_locked", lambda *a, **kw: 0)
        job.run_backup(self.args(tmp_path))
        # If it were still held, the next night would stand down forever.
        RunLock(tmp_path).acquire().release()

    def test_the_lock_is_released_even_when_the_run_raises(self, tmp_path, monkeypatch):
        def boom(*a, **kw):
            raise RuntimeError("the run died")

        monkeypatch.setattr(job, "run_locked", boom)
        with pytest.raises(RuntimeError):
            job.run_backup(self.args(tmp_path))
        RunLock(tmp_path).acquire().release()

    def test_the_state_directory_is_created_if_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(job, "run_locked", lambda *a, **kw: 0)
        fresh = tmp_path / "not" / "there" / "yet"
        job.run_backup(job.parse_args([
            "--env", "prod", "--state-dir", str(fresh),
            "--box-root", "Bloom-Backups/BloomV2-Data-Backup/prod/storage",
        ]))
        assert fresh.is_dir()

    def test_the_exit_code_is_whatever_the_run_returned(self, tmp_path, monkeypatch):
        monkeypatch.setattr(job, "run_locked", lambda *a, **kw: 4)
        assert job.run_backup(self.args(tmp_path)) == 4


class TestTheLedgerIsCopiedToBox:
    """The ledger lives on the host this job exists to survive losing.

    It records which version of every object is on Box, and it is what lets a
    multi-week seed stop and carry on. Without a copy off the host, rebuilding
    the server means re-transferring all eight million objects — and listing
    Box cannot reconstruct it, because the ledger is keyed on each object's
    version and a listing shows only that a path exists.
    """

    @pytest.fixture
    def harness(self, monkeypatch, tmp_path):
        state, tmp_path = TestRunLockedWiresItsPartsTogether().harness.__wrapped__(
            TestRunLockedWiresItsPartsTogether(), monkeypatch, tmp_path
        )
        # publish_report and publish_ledger build their own client from the
        # daemon's credentials rather than taking the one the run already has,
        # so the harness's fake never sees either upload — every harness test
        # has been quietly logging "upload failed: Connection refused" and
        # falling back to the local copy. Substitute the class so the uploads
        # are observable.
        monkeypatch.setattr(job, "RcloneRC", lambda *a, **kw: state["client"])
        return state, tmp_path

    def uploads(self, state):
        return [c for c in state["copied"] if c[1] == "ledger.db"]

    def test_it_is_uploaded_to_its_own_folder(self, harness):
        state, tmp_path = harness
        job.run_locked(TestRunLockedWiresItsPartsTogether().args(tmp_path), tmp_path)
        sent = self.uploads(state)
        assert len(sent) == 1, "the ledger was not uploaded"
        assert sent[0][2] == (
            "Bloom-Backups/BloomV2-Data-Backup/prod/storage/_state/ledger.db"
        ), sent[0][2]

    def test_it_is_not_mixed_in_with_the_objects(self, harness):
        state, tmp_path = harness
        job.run_locked(TestRunLockedWiresItsPartsTogether().args(tmp_path), tmp_path)
        for _, _, dst in self.uploads(state):
            assert "/_state/" in dst, f"the ledger landed among the mirror: {dst}"

    def test_the_uploaded_copy_is_complete(self, harness, monkeypatch, tmp_path):
        """The reason it is closed before being sent.

        SQLite runs in WAL mode, so committed rows sit in ledger.db-wal until
        the file is checkpointed. Uploading ledger.db while the connection is
        open ships a file missing everything the run just recorded — which
        would look fine until the day it was restored.

        So: take a copy of ledger.db ALONE at the moment it is uploaded, the
        way rclone would, and read it back with no WAL beside it.
        """
        import shutil
        import sqlite3

        state, tmp_path = harness
        snapshot = tmp_path / "snapshot" / "ledger.db"
        snapshot.parent.mkdir()
        real_copy = state["client"].copy_file

        def snapshotting_copy(src_fs, src_remote, dst_fs, dst_remote):
            real_copy(src_fs, src_remote, dst_fs, dst_remote)
            if src_remote == "ledger.db":
                shutil.copyfile(tmp_path / "ledger.db", snapshot)

        monkeypatch.setattr(state["client"], "copy_file", snapshotting_copy)
        job.run_locked(TestRunLockedWiresItsPartsTogether().args(tmp_path), tmp_path)

        assert snapshot.exists(), "the ledger was never uploaded"
        conn = sqlite3.connect(str(snapshot))
        copied = conn.execute("SELECT count(*) FROM copied").fetchone()[0]
        runs = conn.execute(
            "SELECT count(*) FROM runs WHERE outcome IS NOT NULL"
        ).fetchone()[0]
        conn.close()
        assert copied == 2, f"the copy is missing rows the run recorded: {copied}"
        assert runs == 1, "the copy does not record the run that made it"

    DEST = "Bloom-Backups/BloomV2-Data-Backup/prod/storage/_state/ledger.db"

    def with_remote_size(self, state, monkeypatch, size):
        """Make Box report a ledger of `size` bytes at the destination."""
        real_stat = state["client"].stat

        def sized(fs, remote):
            if remote == TestTheLedgerIsCopiedToBox.DEST:
                return None if size is None else {"Size": size}
            return real_stat(fs, remote)

        monkeypatch.setattr(state["client"], "stat", sized)

    def test_it_refuses_to_replace_a_larger_copy(self, harness, monkeypatch, caplog):
        """The scenario the whole feature exists for, and would have broken.

        Host dies, gets rebuilt, ledger is empty. The wiki's smoke test copies
        twenty objects — enough to trigger this upload — and without a floor it
        replaces the record of eight million copied objects with a record of
        twenty. The one thing needed to avoid a three-week re-seed, destroyed
        by the first command the operator runs.
        """
        state, tmp_path = harness
        self.with_remote_size(state, monkeypatch, 1_700_000_000)
        code = job.run_locked(
            TestRunLockedWiresItsPartsTogether().args(tmp_path), tmp_path
        )
        assert self.uploads(state) == [], "overwrote a larger ledger on Box"
        assert code == 0, "a refused ledger upload failed the whole run"
        assert "NOT uploaded" in caplog.text
        assert "Restore the Box copy" in caplog.text, "did not say how to recover"

    def test_it_uploads_when_the_copy_on_box_is_smaller(self, harness, monkeypatch):
        state, tmp_path = harness
        self.with_remote_size(state, monkeypatch, 1)
        job.run_locked(TestRunLockedWiresItsPartsTogether().args(tmp_path), tmp_path)
        assert len(self.uploads(state)) == 1, "refused to update a stale copy"

    def test_it_uploads_when_box_has_no_copy_yet(self, harness, monkeypatch):
        state, tmp_path = harness
        self.with_remote_size(state, monkeypatch, None)
        job.run_locked(TestRunLockedWiresItsPartsTogether().args(tmp_path), tmp_path)
        assert len(self.uploads(state)) == 1, "the first upload never happens"

    def test_an_equal_sized_copy_is_still_replaced(self, harness, monkeypatch, tmp_path):
        """Only SMALLER is refused. Equal means the same ledger, and a run that
        copied something has almost certainly changed it."""
        state, tmp_path = harness
        args = TestRunLockedWiresItsPartsTogether().args(tmp_path)
        job.run_locked(args, tmp_path)          # first run creates the ledger
        size = (tmp_path / "ledger.db").stat().st_size
        state["copied"].clear()
        self.with_remote_size(state, monkeypatch, size)
        # Something new to copy, so the upload is reached at all.
        monkeypatch.setattr(
            TestRunLockedWiresItsPartsTogether, "MANIFEST",
            "images\texp-42/c.png\tv3\t100\t2026-08-31T00:00:02+00\n",
        )
        job.run_locked(args, tmp_path)
        assert len(self.uploads(state)) == 1

    def test_a_night_that_copied_nothing_does_not_send_it(self, harness):
        """It is a gigabyte or two once seeded, and an unchanged file."""
        state, tmp_path = harness
        args = TestRunLockedWiresItsPartsTogether().args(tmp_path)
        job.run_locked(args, tmp_path)
        state["copied"].clear()
        job.run_locked(args, tmp_path)   # everything already current
        assert self.uploads(state) == [], "re-sent an unchanged ledger"

    def test_a_crashed_run_does_not_replace_the_good_copy(self, harness, monkeypatch):
        """Crashing AFTER copying, which is the case the guard is for.

        A run that dies before copying anything is already covered by the
        nothing-copied rule; only a run that copied and then crashed can reach
        the upload with a ledger the run never finished writing.
        """
        state, tmp_path = harness

        def boom(*a, **kw):
            raise RuntimeError("Box refused during verification")

        monkeypatch.setattr(job, "verify_sample", boom)
        args = TestRunLockedWiresItsPartsTogether().args(tmp_path, verify=50)
        with pytest.raises(RuntimeError):
            job.run_locked(args, tmp_path)
        assert state["copied"], "nothing was copied, so this proves nothing"
        assert self.uploads(state) == [], "a half-written ledger was uploaded"

    def test_a_run_that_copied_nothing_and_crashed_is_also_skipped(
        self, harness, monkeypatch
    ):
        state, tmp_path = harness

        def boom(*a, **kw):
            raise RuntimeError("preflight could not reach MinIO")

        monkeypatch.setattr(job, "preflight_source", boom)
        with pytest.raises(RuntimeError):
            job.run_locked(TestRunLockedWiresItsPartsTogether().args(tmp_path), tmp_path)
        assert self.uploads(state) == []

    def test_a_failed_upload_neither_fails_the_run_nor_strands_the_container(
        self, harness, monkeypatch, caplog
    ):
        state, tmp_path = harness
        real_copy = state["client"].copy_file

        def refuse_the_ledger(src_fs, src_remote, dst_fs, dst_remote):
            if src_remote == "ledger.db":
                raise RcloneError("box: quota exceeded", retryable=False)
            return real_copy(src_fs, src_remote, dst_fs, dst_remote)

        monkeypatch.setattr(state["client"], "copy_file", refuse_the_ledger)
        code = job.run_locked(TestRunLockedWiresItsPartsTogether().args(tmp_path), tmp_path)
        assert code == 0, "a failed ledger upload failed the whole run"
        assert state["daemon_stopped"], "the rclone container was left behind"
        assert "upload failed" in caplog.text


class TestACrashedRunStillLeavesARecord:
    """`finish_run` sat after the try/except/finally rather than inside it.

    So it ran on every successful run and on no failing one: the `runs` row
    opened by `start_run` kept a NULL finished_at, outcome and stats forever.
    Not dead code — code that runs in the ordinary case and is skipped exactly
    when the record is worth having. The report published to Box named the
    outcome correctly three lines earlier, so the local ledger the job's own
    error messages point operators at was the one place a crash never showed.
    """

    @pytest.fixture
    def harness(self, monkeypatch, tmp_path):
        return TestRunLockedWiresItsPartsTogether().harness.__wrapped__(
            TestRunLockedWiresItsPartsTogether(), monkeypatch, tmp_path
        )

    def crash(self, harness, monkeypatch):
        def boom(*a, **kw):
            raise RuntimeError("preflight could not reach MinIO")

        monkeypatch.setattr(job, "preflight_source", boom)
        state, tmp_path = harness
        args = TestRunLockedWiresItsPartsTogether().args(tmp_path)
        with pytest.raises(RuntimeError, match="preflight"):
            job.run_locked(args, tmp_path)
        return state, tmp_path

    def last_run(self, tmp_path):
        import sqlite3

        conn = sqlite3.connect(str(tmp_path / "ledger.db"))
        row = conn.execute(
            "SELECT finished_at, outcome, stats FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        return row

    def test_the_run_is_recorded_as_finished(self, harness, monkeypatch):
        _, tmp_path = self.crash(harness, monkeypatch)
        finished_at, _, _ = self.last_run(tmp_path)
        assert finished_at is not None, "the row was left open forever"

    def test_the_run_is_recorded_as_an_error(self, harness, monkeypatch):
        _, tmp_path = self.crash(harness, monkeypatch)
        _, outcome, _ = self.last_run(tmp_path)
        assert outcome == "error", f"a crash was recorded as {outcome!r}"

    def test_the_stats_are_recorded_too(self, harness, monkeypatch):
        import json

        _, tmp_path = self.crash(harness, monkeypatch)
        _, _, stats = self.last_run(tmp_path)
        assert stats is not None, "no stats for the run that most needs them"
        assert json.loads(stats)["copied"] == 0

    def test_a_crash_is_not_a_watermark(self, harness, monkeypatch):
        """The row is now filled in, so it must still not count as success."""
        _, tmp_path = self.crash(harness, monkeypatch)
        led = Ledger.open(str(tmp_path / "ledger.db"))
        assert led.last_successful_run() is None
        led.close()

    def test_the_exception_still_reaches_the_caller(self, harness, monkeypatch):
        # Recording the failure must not swallow it — main() turns it into the
        # exit code, and a crash that exits 0 is worse than an open row.
        self.crash(harness, monkeypatch)

    def test_an_ordinary_run_is_still_recorded_ok(self, harness):
        state, tmp_path = harness
        assert job.run_locked(
            TestRunLockedWiresItsPartsTogether().args(tmp_path), tmp_path
        ) == 0
        finished_at, outcome, stats = self.last_run(tmp_path)
        assert finished_at is not None
        assert outcome == "ok"
        assert stats is not None


class TestACollisionIsVisibleInAWholeRun:
    """End to end, because the complaint was silence rather than wrongness.

    A run that quietly copies one of two objects and reports success is the
    failure mode: nothing in the exit code, the ledger, or the report said an
    object had been left behind.
    """

    COMPOSED = "exp/caf\u00e9.png"
    DECOMPOSED = "exp/caf\u0065\u0301.png"

    @pytest.fixture
    def harness(self, monkeypatch, tmp_path):
        return TestRunLockedWiresItsPartsTogether().harness.__wrapped__(
            TestRunLockedWiresItsPartsTogether(), monkeypatch, tmp_path
        )

    def run_it(self, harness, monkeypatch):
        monkeypatch.setattr(
            TestRunLockedWiresItsPartsTogether, "MANIFEST",
            f"images\t{self.COMPOSED}\tv1\t100\t2026-08-31T00:00:00+00\n"
            f"images\t{self.DECOMPOSED}\tv2\t100\t2026-08-31T00:00:01+00\n",
        )
        state, tmp_path = harness
        args = TestRunLockedWiresItsPartsTogether().args(tmp_path)
        code = job.run_locked(args, tmp_path)
        return state, tmp_path, code

    def test_only_one_object_is_copied(self, harness, monkeypatch):
        state, _, _ = self.run_it(harness, monkeypatch)
        assert len(state["copied"]) == 1, (
            "both were copied — the second overwrote the first on Box"
        )

    def test_the_run_exits_five(self, harness, monkeypatch):
        """The wiring, not the function. `exit_code` is unit-tested and
        `totals.collisions` is accumulated — nothing joined the two."""
        _, _, code = self.run_it(harness, monkeypatch)
        assert code == 5, f"a collision exited {code}; the run looked clean"

    def test_the_run_is_recorded_partial(self, harness, monkeypatch):
        """So the watermark is held and the object stays enumerated."""
        import sqlite3

        _, tmp_path, _ = self.run_it(harness, monkeypatch)
        conn = sqlite3.connect(str(tmp_path / "ledger.db"))
        outcome = conn.execute(
            "SELECT outcome FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        conn.close()
        assert outcome == "partial", f"recorded {outcome!r}; the watermark moved"

    def test_the_watermark_does_not_advance(self, harness, monkeypatch):
        """The consequence that matters: a clean record here means the next
        run filters on this run's start time and never lists the object."""
        _, tmp_path, _ = self.run_it(harness, monkeypatch)
        led = Ledger.open(str(tmp_path / "ledger.db"))
        assert led.last_successful_run() is None
        led.close()

    def test_the_run_report_counts_the_collision(self, harness, monkeypatch):
        import json
        import sqlite3

        _, tmp_path, _ = self.run_it(harness, monkeypatch)
        conn = sqlite3.connect(str(tmp_path / "ledger.db"))
        stats = json.loads(
            conn.execute("SELECT stats FROM runs ORDER BY id DESC LIMIT 1").fetchone()[0]
        )
        conn.close()
        assert stats["collisions"] == 1, f"nothing recorded the collision: {stats}"
        assert stats["copied"] == 1

    def test_the_pair_is_named_in_the_log(self, harness, monkeypatch, caplog):
        """The error line says "named at the START of each `skipping` line".
        Nothing asserted those lines are emitted, so that promise could become
        false without a test failing."""
        import logging

        with caplog.at_level(logging.WARNING, logger="bloom_box_object_backup"):
            self.run_it(harness, monkeypatch)
        assert "skipping" in caplog.text, "the pairs are never named"
        assert ascii(self.COMPOSED) in caplog.text, (
            "the holder is not named, or is named unreadably"
        )

    def test_a_dry_run_reports_the_collision_too(self, harness, monkeypatch, caplog):
        """A dry run is what an operator runs first, and it was the one path
        where a refused collision stayed invisible to the summary."""
        import logging

        monkeypatch.setattr(
            TestRunLockedWiresItsPartsTogether, "MANIFEST",
            f"images\t{self.COMPOSED}\tv1\t100\t2026-08-31T00:00:00+00\n"
            f"images\t{self.DECOMPOSED}\tv2\t100\t2026-08-31T00:00:01+00\n",
        )
        state, tmp_path = harness
        args = TestRunLockedWiresItsPartsTogether().args(tmp_path)
        args.dry_run = True   # a store_true flag, not a --flag value pair
        with caplog.at_level(logging.ERROR, logger="bloom_box_object_backup"):
            job.run_locked(args, tmp_path)
        assert state["copied"] == [], "a dry run copied something"
        assert "were NOT backed up" in caplog.text, (
            "the summary greps this phrase; a dry run would read succeeded"
        )

    def test_the_run_tells_the_operator_what_to_do(self, harness, monkeypatch, caplog):
        import logging

        with caplog.at_level(logging.ERROR, logger="bloom_box_object_backup"):
            self.run_it(harness, monkeypatch)
        assert "were NOT backed up" in caplog.text, "the run said nothing"
        assert "rename" in caplog.text.lower(), "did not say how to fix it"

    def test_the_ledger_does_not_claim_both(self, harness, monkeypatch):
        import sqlite3

        _, tmp_path, _ = self.run_it(harness, monkeypatch)
        conn = sqlite3.connect(str(tmp_path / "ledger.db"))
        rows = conn.execute("SELECT name, raw_name FROM copied").fetchall()
        conn.close()
        assert len(rows) == 1, f"two rows for one Box path: {rows}"
        assert rows[0][1] == self.COMPOSED


class TestStoppingActuallyStopsTheWork:
    """Behaviour, not source text.

    This class used to be three greps for the substring `stopping.stopping()`.
    Every one of them stayed green against a dead check (`and False`), against
    the check deleted with the text left in a trailing comment, and against the
    call sites passing a hard-coded `stopped=False`. In other words the flag's
    effect on a run was not tested at all — only that a certain string appeared
    somewhere in the file.

    These drive the real functions with the flag set instead.
    """

    @pytest.fixture(autouse=True)
    def clean_flag(self):
        stopping.reset()
        yield
        stopping.reset()

    class StopOnFirstCopy(FakeRclone):
        """A client that asks the run to stop as its first copy lands.

        Stands in for a signal arriving mid-run, without the timing races of
        actually sending one.
        """

        def copy_file(self, src_fs, src_remote, dst_fs, dst_remote):
            super().copy_file(src_fs, src_remote, dst_fs, dst_remote)
            stopping._request_stop(15, None)

    def test_the_copier_stops_handing_out_work(self, ledger):
        client = self.StopOnFirstCopy()
        objects = [obj(name=f"exp/{n}.png", version=f"v{n}") for n in range(25)]
        copied, failed = run_copy(client, objects, ledger, workers=1)
        assert failed == 0
        assert copied == 1, f"kept going after the stop — copied {copied} of 25"
        assert len(client.calls) == 1

    def test_the_object_in_flight_is_recorded(self, ledger):
        # It must not be re-copied on the next run, and must not be lost.
        client = self.StopOnFirstCopy()
        objects = [obj(name=f"exp/{n}.png", version=f"v{n}") for n in range(5)]
        run_copy(client, objects, ledger, workers=1)
        ledger.commit()
        first = objects[0]
        found = ledger.versions_for([first.ledger_key])
        assert found[first.ledger_key].version == first.version

    def test_what_was_not_reached_is_still_pending(self, ledger):
        # The rest must remain uncopied so a later run picks them up.
        client = self.StopOnFirstCopy()
        objects = [obj(name=f"exp/{n}.png", version=f"v{n}") for n in range(5)]
        run_copy(client, objects, ledger, workers=1)
        ledger.commit()
        remaining = build_plan(objects, ledger.copied_versions())
        assert len(remaining.copies) == 4

    def test_a_second_run_carries_on_from_there(self, ledger):
        # The whole promise of stopping: run it again and it continues.
        objects = [obj(name=f"exp/{n}.png", version=f"v{n}") for n in range(5)]
        run_copy(self.StopOnFirstCopy(), objects, ledger, workers=1)
        ledger.commit()
        stopping.reset()

        second = FakeRclone()
        copied, failed = run_copy(second, objects, ledger, workers=1)
        assert failed == 0
        assert copied == 4, "did not resume where the first run stopped"
        assert len(second.calls) == 4


class TestStoppingReachesTheOutcomeAndTheExitCode:
    """The call sites, which testing the pure functions cannot reach.

    `run_outcome` and `exit_code` were both covered, but only by calling them
    directly with `stopped=True`. Replacing `stopped=stopping.stopping()` with
    `stopped=False` at either call site left the whole suite green — so a
    stopped run would have recorded itself clean and exited 0, which are
    exactly the two things this must not do.
    """

    @pytest.fixture(autouse=True)
    def clean_flag(self):
        stopping.reset()
        yield
        stopping.reset()

    @pytest.fixture
    def harness(self, monkeypatch, tmp_path):
        return TestRunLockedWiresItsPartsTogether().harness.__wrapped__(
            TestRunLockedWiresItsPartsTogether(), monkeypatch, tmp_path
        )

    def test_a_run_stopped_mid_copy_records_partial(self, harness):
        import sqlite3

        state, tmp_path = harness
        stopping._request_stop(15, None)
        args = TestRunLockedWiresItsPartsTogether().args(tmp_path)
        job.run_locked(args, tmp_path)
        outcome = sqlite3.connect(tmp_path / "ledger.db").execute(
            "SELECT outcome FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert outcome and outcome[0] == "partial", (
            "a stopped run recorded itself clean — the watermark would advance "
            "past objects it never reached"
        )

    def test_a_run_stopped_mid_copy_exits_three(self, harness):
        state, tmp_path = harness
        stopping._request_stop(15, None)
        args = TestRunLockedWiresItsPartsTogether().args(tmp_path)
        assert job.run_locked(args, tmp_path) == 3

    def test_an_unstopped_run_is_unaffected(self, harness):
        state, tmp_path = harness
        args = TestRunLockedWiresItsPartsTogether().args(tmp_path)
        assert job.run_locked(args, tmp_path) == 0


class TestTheEntryPointInstallsTheHandlers:
    """Driven through `main`, not asserted against its source.

    `stopping_test.py` sends real signals, but its fixture calls
    `install_handlers()` itself — so it proves the module works, not that the
    job switches it on. That is the same gap its docstring accuses the pattern
    it copied of having.
    """

    def test_main_installs_them_before_doing_anything(self, monkeypatch, tmp_path):
        installed = []
        monkeypatch.setattr(
            job.stopping, "install_handlers", lambda: installed.append("yes")
        )
        # Fail immediately afterwards: we only care that it happened, and that
        # it happened before any work started.
        monkeypatch.setattr(
            job, "run_backup",
            lambda args: (_ for _ in ()).throw(job.lib.BackupError("stop here")),
        )
        code = job.main(["--env", "prod", "--state-dir", str(tmp_path)])
        assert installed == ["yes"], "main did not install the stop handlers"
        assert code == 2


class TestStoppingBetweenBatches:
    """The check in `copy_manifest`, which needs more than one batch to matter.

    Every other stopping test drives `copy_all` directly, or uses a manifest
    small enough to be a single batch — so making this check dead left the
    suite green. What it has to be caught doing is refusing to *plan* a further
    batch: the per-object check already stops the copying, so counting copied
    objects proves nothing here. A real seed plans 20,000 at a time, reading
    the ledger for each, and without this a stop waits out the whole remaining
    plan before anything notices.
    """

    @pytest.fixture(autouse=True)
    def clean_flag(self):
        stopping.reset()
        yield
        stopping.reset()

    def test_no_further_batch_is_planned_after_a_stop(self, monkeypatch, tmp_path):
        # Two objects per batch rather than 20,000, so six objects make three
        # batches instead of one.
        monkeypatch.setattr(job, "BATCH_SIZE", 2)

        manifest = tmp_path / "manifest.tsv"
        manifest.write_text("".join(
            f"images\texp/{n}.png\tv{n}\t100\t2026-08-31T00:00:0{n}+00\n"
            for n in range(6)
        ))
        led = Ledger.open(str(tmp_path / "ledger.db"))

        batches_run = []
        real_copy_all = job.copy_all

        def counting_copy_all(*a, **kw):
            batches_run.append(len(a[1].copies))
            return real_copy_all(*a, **kw)

        monkeypatch.setattr(job, "copy_all", counting_copy_all)

        class StopOnFirstCopy(FakeRclone):
            def copy_file(self, src_fs, src_remote, dst_fs, dst_remote):
                super().copy_file(src_fs, src_remote, dst_fs, dst_remote)
                stopping._request_stop(15, None)

        args = job.parse_args([
            "--env", "prod",
            "--state-dir", str(tmp_path),
            "--box-root", "Bloom-Backups/BloomV2-Data-Backup/prod/storage",
            "--minio-bucket", "bloom-storage",
            "--workers", "1",
        ])
        totals = job.Totals()
        job.copy_manifest(StopOnFirstCopy(), manifest, led, MINIO, BOX_FS, args, totals)
        led.close()

        # The stop lands during the first batch, so the second and third are
        # never planned or handed out at all.
        assert len(batches_run) == 1, (
            f"kept planning batches after the stop — ran {len(batches_run)} of 3"
        )
        assert totals.copied == 1, f"copied {totals.copied}, expected the one in flight"
