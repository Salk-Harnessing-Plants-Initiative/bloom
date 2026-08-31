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
MINIO = MinioSource("http://supabase-minio:9000", "root", "secret")
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
