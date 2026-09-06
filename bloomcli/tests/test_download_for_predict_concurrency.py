"""`cyl download-for-predict` / `batch-download-for-predict` fetch a scan's frames several at
a time, mirroring `cyl download`'s worker pool (PR #623, bloom #652) and reusing `download_to`
(bloom #652 design.md) instead of re-deriving download+atomic-write+retry logic.

Covers the `--workers` flag, that the downloads really do overlap, that a concurrent run still
assembles `frame_bytes` in DB frame_number order (the sidecar checksum depends on this — see
`compute_checksum`), that the pool runs *inside* the per-scan lock `stage_one_scan` already holds
(bloom #655), and disk-full protection (now present at any `--workers` value, including 1).
"""

from __future__ import annotations

import errno
import threading
import time
from pathlib import Path

from click.testing import CliRunner
from test_cyl_download_for_predict import SCAN, _FakeClient, _patch_batch, _patch_common

import bloomctl._download as shared_dl
import bloomctl.auth as auth
import bloomctl.cyl.download_for_predict as dfp
from bloomctl.cli import cli


def _images(count: int) -> list[dict]:
    return [
        {"id": 1000 + i, "frame_number": i, "object_path": f"cyl-images/{i}.png"}
        for i in range(count)
    ]


# --- 1. Per-frame worker rebuilt on download_to ------------------------------


def test_malformed_row_is_isolated_not_raised(tmp_path):
    image = {"frame_number": 3}  # missing object_path

    result, frame_bytes = dfp._download_one_frame_for_predict(_FakeClient(), SCAN, image, tmp_path)

    assert result.ok is False
    assert frame_bytes is None


def test_well_formed_frame_downloads_and_returns_bytes(tmp_path):
    image = {"id": 1001, "frame_number": 0, "object_path": "cyl-images/a.png"}

    result, frame_bytes = dfp._download_one_frame_for_predict(_FakeClient(), SCAN, image, tmp_path)

    assert result.ok is True
    assert (tmp_path / "0.png").read_bytes() == b"bytes::cyl-images/a.png"
    assert frame_bytes == b"bytes::cyl-images/a.png"


def test_returned_bytes_come_from_disk_not_the_download_response(tmp_path, monkeypatch):
    """Proves the "read-back-off-disk" design decision (bloom #652 design.md): if the worker
    were (incorrectly) implemented to return the in-memory `download_object` response instead
    of `dest.read_bytes()`, this would fail — the two are made to differ deliberately here."""
    image = {"id": 1001, "frame_number": 0, "object_path": "cyl-images/a.png"}
    on_disk = b"what-atomic_write_bytes-actually-wrote"

    def _fake_write(path, data):
        Path(path).write_bytes(on_disk)

    monkeypatch.setattr(shared_dl, "atomic_write_bytes", _fake_write)

    result, frame_bytes = dfp._download_one_frame_for_predict(_FakeClient(), SCAN, image, tmp_path)

    assert result.ok is True
    assert frame_bytes == on_disk
    assert frame_bytes != b"bytes::cyl-images/a.png"  # the fake bucket's in-memory response


def test_download_failure_returns_no_bytes_and_leaves_no_temp_file(tmp_path):
    image = {"id": 1001, "frame_number": 0, "object_path": "cyl-images/a.png"}

    class _FailingBucket:
        def download(self, object_path):
            raise RuntimeError("simulated storage failure")

    client = _FakeClient()
    client.storage._bucket = _FailingBucket()

    result, frame_bytes = dfp._download_one_frame_for_predict(client, SCAN, image, tmp_path)

    assert result.ok is False
    assert frame_bytes is None
    assert list(tmp_path.glob(".dl-*")) == []


def test_post_write_read_failure_is_a_failed_result_not_a_raised_exception(tmp_path, monkeypatch):
    """review finding: `dest.read_bytes()` after a successful write was unguarded, contradicting
    this function's own "never raises" docstring — a transient post-write read failure (disk
    fault, permissions change, an AV lock on Windows) would otherwise escape as a raw exception."""
    image = {"id": 1001, "frame_number": 0, "object_path": "cyl-images/a.png"}
    real_read_bytes = Path.read_bytes

    def _fail_read_bytes(self):
        if self.name == "0.png":
            raise OSError("simulated post-write read failure")
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", _fail_read_bytes)

    result, frame_bytes = dfp._download_one_frame_for_predict(_FakeClient(), SCAN, image, tmp_path)

    assert result.ok is False
    assert frame_bytes is None
    assert "could not read it back" in result.error


def test_a_read_back_failure_in_the_pool_does_not_affect_sibling_frames(tmp_path, monkeypatch):
    """review finding: the direct-call test above proves the failure is caught, but not that it
    stays isolated when routed through the real concurrent pool (workers>1) — mirrors the
    existing sibling-frame-isolation pattern already used for storage and disk-full failures."""
    images = _images(4)
    real_read_bytes = Path.read_bytes

    def _fail_read_bytes_for_frame_2(self):
        if self.name == "2.png":
            raise OSError("simulated post-write read failure")
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", _fail_read_bytes_for_frame_2)

    result, frame_bytes = dfp.download_frames_for_predict(
        _FakeClient(), SCAN, images, tmp_path, workers=4
    )

    assert result.ok == 3 and result.failed == 1
    assert len(frame_bytes) == 3
    assert result.frames[2].ok is False and "could not read it back" in result.frames[2].error
    assert all(f.ok for i, f in enumerate(result.frames) if i != 2)


# --- 2. Bounded concurrent pool via fetch_all --------------------------------


def test_frames_really_are_downloaded_in_parallel(tmp_path):
    """Catches the downloads quietly going back to one at a time."""
    images = _images(8)
    barrier = threading.Barrier(4, timeout=5)

    class _SlowBucket:
        def download(self, object_path):
            barrier.wait()  # only passes if >=4 threads are in flight together
            return f"bytes::{object_path}".encode()

    client = _FakeClient()
    client.storage._bucket = _SlowBucket()

    started = time.monotonic()
    result, _ = dfp.download_frames_for_predict(client, SCAN, images, tmp_path, workers=4)

    assert result.ok == 8  # a serial run would deadlock and time out on the barrier
    assert time.monotonic() - started < 5


def test_frame_bytes_are_returned_in_db_order_not_completion_order(tmp_path):
    """The checksum hashes `frame_bytes` in DB frame_number order (`compute_checksum`'s own
    contract) — this must hold even when the last-submitted frame is the first to finish."""
    count = 6
    images = _images(count)

    class _ReverseOrderBucket:
        """Frame 0 sleeps longest, frame `count - 1` returns first."""

        def download(self, object_path):
            index = int(object_path.rsplit("/", 1)[-1].split(".")[0])
            time.sleep((count - index) * 0.02)
            return f"bytes::{object_path}".encode()

    client = _FakeClient()
    client.storage._bucket = _ReverseOrderBucket()

    result, frame_bytes = dfp.download_frames_for_predict(client, SCAN, images, tmp_path, workers=4)

    assert result.ok == count
    expected = [f"bytes::cyl-images/{i}.png".encode() for i in range(count)]
    assert frame_bytes == expected
    assert dfp.compute_checksum(frame_bytes) == dfp.compute_checksum(expected)


def test_one_bad_frame_does_not_abort_a_concurrent_download(tmp_path):
    images = _images(6)

    class _OneBadBucket:
        def download(self, object_path):
            if object_path.endswith("3.png"):
                raise RuntimeError("500 storage error")
            return f"bytes::{object_path}".encode()

    client = _FakeClient()
    client.storage._bucket = _OneBadBucket()

    result, frame_bytes = dfp.download_frames_for_predict(client, SCAN, images, tmp_path, workers=4)

    assert result.ok == 5 and result.failed == 1
    assert len(frame_bytes) == 5
    assert result.frames[3].frame_number == 3 and not result.frames[3].ok


def test_malformed_row_survives_the_pool_without_aborting_other_frames(tmp_path):
    images = _images(4)
    images[2] = {"id": 9999, "object_path": "cyl-images/bad.png"}  # missing frame_number

    result, frame_bytes = dfp.download_frames_for_predict(
        _FakeClient(), SCAN, images, tmp_path, workers=4
    )

    assert result.ok == 3 and result.failed == 1
    assert len(frame_bytes) == 3


def test_workers_one_runs_sequentially(tmp_path, monkeypatch):
    """`--workers 1` should download in order on one thread, with no pool at all."""
    images = _images(3)
    used: list[str] = []
    real_pool = shared_dl.ThreadPoolExecutor

    class _Spy(real_pool):
        def __init__(self, *a, **k):
            used.append("pool")
            super().__init__(*a, **k)

    monkeypatch.setattr(shared_dl, "ThreadPoolExecutor", _Spy)

    result, frame_bytes = dfp.download_frames_for_predict(
        _FakeClient(), SCAN, images, tmp_path, workers=1
    )

    assert result.ok == 3
    assert used == []
    assert frame_bytes == [f"bytes::cyl-images/{i}.png".encode() for i in range(3)]


def test_the_pool_never_exceeds_the_ceiling_even_via_a_direct_call(tmp_path, monkeypatch):
    """The limit has to hold when the function is called directly, not just via the flag."""
    images = _images(200)
    seen: dict = {}
    real_pool = shared_dl.ThreadPoolExecutor

    class _Spy(real_pool):
        def __init__(self, max_workers=None, **kwargs):
            seen["max_workers"] = max_workers
            super().__init__(max_workers=max_workers, **kwargs)

    monkeypatch.setattr(shared_dl, "ThreadPoolExecutor", _Spy)

    result, _ = dfp.download_frames_for_predict(_FakeClient(), SCAN, images, tmp_path, workers=1000)

    assert seen["max_workers"] == dfp.MAX_WORKERS
    assert result.ok == 200


def test_the_pool_is_never_larger_than_the_work(tmp_path, monkeypatch):
    images = _images(3)
    seen: dict = {}
    real_pool = shared_dl.ThreadPoolExecutor

    class _Spy(real_pool):
        def __init__(self, max_workers=None, **kwargs):
            seen["max_workers"] = max_workers
            super().__init__(max_workers=max_workers, **kwargs)

    monkeypatch.setattr(shared_dl, "ThreadPoolExecutor", _Spy)

    dfp.download_frames_for_predict(_FakeClient(), SCAN, images, tmp_path, workers=32)

    assert seen["max_workers"] == 3  # 3 frames, so 3 threads — not 32


# --- 3. -n/--workers CLI option (single-scan command) ------------------------


def _spy_on_download_frames_for_predict(monkeypatch):
    seen: dict = {}
    real = dfp.download_frames_for_predict

    def _spy(client, scan, images, scan_dir, **kwargs):
        seen.update(kwargs)
        return real(client, scan, images, scan_dir, **kwargs)

    monkeypatch.setattr(dfp, "download_frames_for_predict", _spy)
    return seen


def test_cli_workers_flag_reaches_download_frames_for_predict(tmp_path, monkeypatch):
    _patch_common(monkeypatch)
    seen = _spy_on_download_frames_for_predict(monkeypatch)
    out = tmp_path / "out"

    result = CliRunner().invoke(
        cli, ["cyl", "download-for-predict", "1", str(out), "--workers", "3"]
    )

    assert result.exit_code == 0, result.output
    assert seen["workers"] == 3


def test_cli_defaults_to_the_documented_worker_count(tmp_path, monkeypatch):
    _patch_common(monkeypatch)
    seen = _spy_on_download_frames_for_predict(monkeypatch)
    out = tmp_path / "out"

    result = CliRunner().invoke(cli, ["cyl", "download-for-predict", "1", str(out)])

    assert result.exit_code == 0, result.output
    assert seen["workers"] == dfp.DEFAULT_WORKERS


def test_cli_workers_boundary_values(tmp_path, monkeypatch):
    _patch_common(monkeypatch)
    for bad in ("0", "65", "-2"):
        out = tmp_path / f"out-bad-{bad}"
        result = CliRunner().invoke(cli, ["cyl", "download-for-predict", "1", str(out), "-n", bad])
        assert result.exit_code != 0, f"--workers {bad} should be rejected"

    for good in ("1", "64"):
        out = tmp_path / f"out-good-{good}"
        result = CliRunner().invoke(cli, ["cyl", "download-for-predict", "1", str(out), "-n", good])
        assert result.exit_code == 0, f"--workers {good} should be accepted: {result.output}"


# --- 3. -n/--workers CLI option (batch command) -------------------------------


def test_stage_one_scan_accepts_and_forwards_workers(tmp_path, monkeypatch):
    _patch_batch(monkeypatch)
    seen = _spy_on_download_frames_for_predict(monkeypatch)
    client = _FakeClient()

    result = dfp.stage_one_scan(client, 1, tmp_path, workers=6)

    assert result.status == "ok"
    assert seen["workers"] == 6


def test_batch_cli_workers_flag_reaches_every_stage_one_scan_call(tmp_path, monkeypatch):
    _patch_batch(monkeypatch)
    seen: dict = {}
    real = dfp.stage_one_scan

    def _spy(client, scan_id, out_dir, **kwargs):
        seen[scan_id] = kwargs.get("workers")
        return real(client, scan_id, out_dir, **kwargs)

    monkeypatch.setattr(dfp, "stage_one_scan", _spy)
    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[1, 2]", encoding="utf-8")
    out = tmp_path / "out"

    result = CliRunner().invoke(
        cli,
        [
            "cyl",
            "batch-download-for-predict",
            str(out),
            "--scan-ids-file",
            str(ids_file),
            "--workers",
            "5",
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen == {1: 5, 2: 5}


def test_batch_cli_workers_really_run_concurrently_per_scan(tmp_path, monkeypatch):
    """The batch command's analog of test_frames_really_are_downloaded_in_parallel — proves
    concurrency through the full CLI entry point, not just at download_frames_for_predict."""
    _patch_batch(monkeypatch, scan_id_to_images={1: _images(8), 2: _images(8)})
    barrier = threading.Barrier(4, timeout=5)

    class _SlowBucket:
        def download(self, object_path):
            barrier.wait()
            return f"bytes::{object_path}".encode()

    client = _FakeClient()
    client.storage._bucket = _SlowBucket()
    monkeypatch.setattr(auth, "make_authed_client", lambda creds: client)

    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[1, 2]", encoding="utf-8")
    out = tmp_path / "out"

    result = CliRunner().invoke(
        cli,
        [
            "cyl",
            "batch-download-for-predict",
            str(out),
            "--scan-ids-file",
            str(ids_file),
            "--workers",
            "4",
        ],
    )

    assert result.exit_code == 0, result.output


def test_batch_cli_defaults_to_the_documented_worker_count(tmp_path, monkeypatch):
    _patch_batch(monkeypatch)
    seen: dict = {}
    real = dfp.stage_one_scan

    def _spy(client, scan_id, out_dir, **kwargs):
        seen.update(kwargs)
        return real(client, scan_id, out_dir, **kwargs)

    monkeypatch.setattr(dfp, "stage_one_scan", _spy)
    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[1]", encoding="utf-8")
    out = tmp_path / "out"

    result = CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file)]
    )

    assert result.exit_code == 0, result.output
    assert seen["workers"] == dfp.DEFAULT_WORKERS


def test_batch_cli_workers_boundary_values(tmp_path, monkeypatch):
    _patch_batch(monkeypatch)
    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[1]", encoding="utf-8")

    for bad in ("0", "65", "-2"):
        out = tmp_path / f"out-bad-{bad}"
        result = CliRunner().invoke(
            cli,
            [
                "cyl",
                "batch-download-for-predict",
                str(out),
                "--scan-ids-file",
                str(ids_file),
                "-n",
                bad,
            ],
        )
        assert result.exit_code != 0, f"--workers {bad} should be rejected"

    for good in ("1", "64"):
        out = tmp_path / f"out-good-{good}"
        result = CliRunner().invoke(
            cli,
            [
                "cyl",
                "batch-download-for-predict",
                str(out),
                "--scan-ids-file",
                str(ids_file),
                "-n",
                good,
            ],
        )
        assert result.exit_code == 0, f"--workers {good} should be accepted: {result.output}"


# --- 4. Disk-full stop wiring -------------------------------------------------


def test_disk_full_stops_further_sequential_frames(tmp_path, monkeypatch):
    """Deterministic (workers=1) proof that a disk-full write failure short-circuits queued
    frames instead of letting each remaining frame independently attempt and fail. `workers=1`
    is used deliberately so "not yet started" is ordering-deterministic, not timing-dependent."""
    images = _images(3)
    calls: list[str] = []
    real_atomic_write = shared_dl.atomic_write_bytes

    def _write_or_fail_on_frame_1(path, data):
        if "1.png" in str(path):
            raise OSError(errno.ENOSPC, "No space left on device")
        real_atomic_write(path, data)

    class _TrackingBucket:
        def download(self, object_path):
            calls.append(object_path)
            return f"bytes::{object_path}".encode()

    monkeypatch.setattr(shared_dl, "atomic_write_bytes", _write_or_fail_on_frame_1)
    client = _FakeClient()
    client.storage._bucket = _TrackingBucket()

    result, _ = dfp.download_frames_for_predict(client, SCAN, images, tmp_path, workers=1)

    assert "cyl-images/2.png" not in calls  # frame 2's download must never be attempted
    assert result.frames[0].ok is True  # frame 0 succeeded before the disk filled
    assert result.frames[1].ok is False
    assert result.frames[2].ok is False
    assert "nowhere left to write" in result.frames[2].error


def test_disk_full_is_surfaced_on_the_result_and_in_both_commands_error_messages(
    tmp_path, monkeypatch
):
    """review finding: `DownloadResult.disk_full` was never set here, unlike `cyl download`'s
    and `plate download`'s identical orchestrators — so an operator hitting a full disk saw the
    same generic message as any other transient failure, with no "disk filled up" wording."""
    images = _images(2)

    def _always_fail(path, data):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(shared_dl, "atomic_write_bytes", _always_fail)
    client = _FakeClient()

    result, _ = dfp.download_frames_for_predict(client, SCAN, images, tmp_path, workers=1)
    assert result.disk_full is True

    monkeypatch.setattr(dfp, "fetch_scan", lambda c, scan_id: {**SCAN, "scan_id": scan_id})
    monkeypatch.setattr(dfp, "fetch_images", lambda c, scan_id: _images(2))
    scan_result = dfp.stage_one_scan(client, 1, tmp_path)
    assert scan_result.status == "failed"
    assert "disk filled up" in scan_result.error


def test_cli_disk_full_error_message_names_the_cause(tmp_path, monkeypatch):
    """review finding: the test above covers `stage_one_scan`'s (batch path) message, but the
    single-scan `download-for-predict` command's own `click.ClickException` — the other call
    site this fix touches — was never exercised end to end through the actual CLI."""
    _patch_common(monkeypatch, images=_images(2))

    def _always_fail(path, data):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(shared_dl, "atomic_write_bytes", _always_fail)
    out = tmp_path / "out"

    result = CliRunner().invoke(cli, ["cyl", "download-for-predict", "1", str(out)])

    assert result.exit_code != 0
    assert "disk filled up" in result.output


def test_disk_full_during_concurrency_does_not_abort_an_already_inflight_frame(
    tmp_path, monkeypatch
):
    """Exercises `download_to`'s existing "check `stop` only before starting, never interrupt
    an in-flight call" contract through the new pool — a barrier guarantees both frames'
    downloads are genuinely in-flight together before either write completes."""
    images = _images(2)
    barrier = threading.Barrier(2, timeout=5)
    real_atomic_write = shared_dl.atomic_write_bytes

    def _write_or_fail_on_frame_0(path, data):
        if "0.png" in str(path):
            raise OSError(errno.ENOSPC, "No space left on device")
        real_atomic_write(path, data)

    class _SlowBucket:
        def download(self, object_path):
            barrier.wait()  # both frames' downloads must be in flight together
            return f"bytes::{object_path}".encode()

    monkeypatch.setattr(shared_dl, "atomic_write_bytes", _write_or_fail_on_frame_0)
    client = _FakeClient()
    client.storage._bucket = _SlowBucket()

    result, _ = dfp.download_frames_for_predict(client, SCAN, images, tmp_path, workers=2)

    assert result.frames[0].ok is False  # the disk-full frame
    assert result.frames[1].ok is True  # already in flight when stop was set; not aborted


def test_disk_full_in_one_scan_does_not_leak_into_the_next_scan(tmp_path, monkeypatch):
    real_atomic_write = shared_dl.atomic_write_bytes

    def _write_or_fail_for_scan_1(path, data):
        if "scan_1" in str(path):
            raise OSError(errno.ENOSPC, "No space left on device")
        real_atomic_write(path, data)

    monkeypatch.setattr(shared_dl, "atomic_write_bytes", _write_or_fail_for_scan_1)
    monkeypatch.setattr(dfp, "fetch_scan", lambda c, scan_id: {**SCAN, "scan_id": scan_id})
    monkeypatch.setattr(dfp, "fetch_images", lambda c, scan_id: _images(2))
    client = _FakeClient()

    result_1 = dfp.stage_one_scan(client, 1, tmp_path)
    result_2 = dfp.stage_one_scan(client, 2, tmp_path)

    assert result_1.status == "failed"
    assert result_2.status == "ok"


# --- 5. Lock composition (bloom #655) -----------------------------------------


def test_stage_one_scan_holds_the_lock_for_the_whole_concurrent_download(tmp_path, monkeypatch):
    """The frame-fetch pool runs *inside* `stage_one_scan`'s already-acquired per-scan lock —
    concurrency within the invocation must not make the lock appear released (or be
    re-acquired) while frames are still in flight."""
    monkeypatch.setattr(dfp, "fetch_scan", lambda c, scan_id: {**SCAN, "scan_id": scan_id})
    monkeypatch.setattr(dfp, "fetch_images", lambda c, scan_id: _images(4))
    lock_path = tmp_path / ".locks" / "scan_1.lock"
    barrier = threading.Barrier(4, timeout=5)

    class _SlowBucket:
        def download(self, object_path):
            assert lock_path.is_file(), "lock must still be held while frames are downloading"
            barrier.wait()  # only passes if >=4 threads are in flight together
            return f"bytes::{object_path}".encode()

    client = _FakeClient()
    client.storage._bucket = _SlowBucket()

    result = dfp.stage_one_scan(client, 1, tmp_path, workers=4)

    assert result.status == "ok"
    assert not lock_path.exists()  # released once the (now faster) critical section completes


def test_stage_one_scan_releases_the_lock_after_a_disk_full_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(dfp, "fetch_scan", lambda c, scan_id: {**SCAN, "scan_id": scan_id})
    monkeypatch.setattr(dfp, "fetch_images", lambda c, scan_id: _images(2))

    def _always_fail(path, data):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(shared_dl, "atomic_write_bytes", _always_fail)
    client = _FakeClient()
    lock_path = tmp_path / ".locks" / "scan_1.lock"

    result = dfp.stage_one_scan(client, 1, tmp_path)

    assert result.status == "failed"
    assert not lock_path.exists()
