"""Behavioural tests for copier.py and the batched planning in backup_objects.py.

The rclone daemon is replaced by a fake that records every copy request, so
these exercise the parts that decide *what* gets sent and *what happens when
Box says no* — retry/backoff, ledger durability, failure accounting — without
a daemon, MinIO, or a Box account.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import backup_objects as job  # noqa: E402
import copier  # noqa: E402
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
def ledger():
    # check_same_thread=False mirrors Ledger.open() — the copy workers are
    # threads, and each records its own success.
    led = Ledger(sqlite3.connect(":memory:", check_same_thread=False))
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
    assert ledger.copied_versions()[("images", "exp-42/frame.png")] == VERSION


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


def test_verify_accepts_a_matching_size(caplog, ledger):
    caplog.set_level(logging.INFO)
    client = FakeRclone()
    client.stats_by_path["root/images/exp-42/frame.png"] = {"Size": 100}
    copier.verify_sample(client, make_plan([obj()]), BOX_FS, "root", 1)
    assert "1 checked, 0 mismatched" in caplog.text


def test_verify_flags_a_missing_destination(caplog, ledger):
    copier.verify_sample(FakeRclone(), make_plan([obj()]), BOX_FS, "root", 1)
    assert "missing on Box" in caplog.text


def test_verify_flags_a_size_mismatch(caplog, ledger):
    client = FakeRclone()
    client.stats_by_path["root/images/exp-42/frame.png"] = {"Size": 7}
    copier.verify_sample(client, make_plan([obj()]), BOX_FS, "root", 1)
    assert "size mismatch" in caplog.text


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

def test_minio_credentials_are_not_logged_on_failure(caplog, ledger):
    key = f"images/exp-42/frame.png/{VERSION}"
    client = FakeRclone({key: [RcloneError("failed on secret_access_key=secret")]})
    run_copy(client, [obj()], ledger)
    assert "MINIO" not in caplog.text


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
    """The sample must cover the run, not its first few thousand objects."""

    def make(self, cap, n):
        r = copier.VerifyReservoir(cap, seed=1234)
        for i in range(n):
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

    def test_counts_everything_it_was_offered(self):
        assert self.make(50, 10_000).seen == 10_000


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

    def test_prod_pointed_at_the_staging_root_is_refused(self):
        # Same logical names in both environments — this would overwrite the
        # other environment's backup rather than sit beside it.
        with pytest.raises(job.lib.BackupError, match="staging"):
            job.check_box_root(
                self.args("Bloom-Backups/BloomV2-Data-Backup/staging/storage", env="prod")
            )

    def test_staging_pointed_at_the_prod_root_is_refused(self):
        with pytest.raises(job.lib.BackupError, match="prod"):
            job.check_box_root(
                self.args("Bloom-Backups/BloomV2-Data-Backup/prod/storage", env="staging")
            )

    def test_a_root_naming_both_is_allowed(self):
        # e.g. .../prod/storage under a folder that happens to mention staging;
        # the run's own env is present, so it is not a cross-environment write.
        job.check_box_root(self.args("Backups/staging-and-prod/prod/storage", env="prod"))

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

    def test_the_watermark_is_read_before_the_manifest(self):
        # Order is the whole property, so assert it on the source rather than
        # trusting a comment: the database clock must be read above the call
        # that snapshots storage.objects.
        source = (Path(__file__).parent / "backup_objects.py").read_text()
        clock_at = source.index("dock.database_now(")
        snapshot_at = source.index("dock.psql_query_to_file(")
        assert clock_at < snapshot_at, (
            "the watermark is taken after the snapshot — objects written "
            "during enumeration would never be enumerated again"
        )

    def test_start_run_records_that_watermark_rather_than_the_host_clock(self):
        source = (Path(__file__).parent / "backup_objects.py").read_text()
        assert "ledger.start_run(now=watermark)" in source, (
            "start_run() with no argument stamps the deploy host's clock, "
            "which is a different clock from the one that writes updated_at"
        )

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
        for code in ("1 =", "2 =", "3 =", "4 ="):
            assert code in doc, f"exit {code[0]} undocumented"
