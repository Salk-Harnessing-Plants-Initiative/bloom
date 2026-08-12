"""`cyl download` fetches several frames at once, with the count under the user's control.

Covers ordering, the `--workers` flag, that the downloads really do overlap, and that resume
still behaves when many are in flight.
"""

from __future__ import annotations

import threading
import time

from click.testing import CliRunner
from test_download_metadata import SCAN
from test_download_session_resume import CREDS, _Client, _frame, _images

import bloomctl._download as shared_dl
import bloomctl.auth as auth
import bloomctl.cyl.download as dl
from bloomctl.cli import cli

SCAN_B = {**SCAN, "scan_id": 2, "qr_code": "QR-2"}


def _frame_b(tmp_path, number: int):
    return tmp_path / f"images/Wave2/Day14_2026-05-11/QR-2/{number}.png"


# --- ordering & worker count ------------------------------------------------


def test_concurrent_download_preserves_frame_order(tmp_path, monkeypatch):
    """The log should read the same every run, whatever order the threads finish in."""
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(12))

    result = dl.download_images(_Client(), [SCAN], tmp_path, workers=8)

    assert result.ok == 12
    assert [f.frame_number for f in result.frames] == list(range(12))


def test_concurrent_download_preserves_order_across_scans(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(4))

    result = dl.download_images(_Client(), [SCAN, SCAN_B], tmp_path, workers=8)

    assert [(f.scan_id, f.frame_number) for f in result.frames] == [
        (1, 0), (1, 1), (1, 2), (1, 3), (2, 0), (2, 1), (2, 2), (2, 3),
    ]  # fmt: skip
    assert _frame(tmp_path, 3).exists() and _frame_b(tmp_path, 3).exists()


def test_workers_one_runs_sequentially(tmp_path, monkeypatch):
    """`--workers 1` should download in order on one thread, with no pool at all."""
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(3))
    used: list[str] = []
    real_pool = shared_dl.ThreadPoolExecutor

    class _Spy(real_pool):
        def __init__(self, *a, **k):
            used.append("pool")
            super().__init__(*a, **k)

    monkeypatch.setattr(shared_dl, "ThreadPoolExecutor", _Spy)

    result = dl.download_images(_Client(), [SCAN], tmp_path, workers=1)

    assert result.ok == 3
    assert used == []


def test_the_pool_never_exceeds_the_ceiling_even_via_a_direct_call(tmp_path, monkeypatch):
    """The limit has to hold when the function is called directly, not just via the flag."""
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(200))
    seen: dict = {}
    real_pool = shared_dl.ThreadPoolExecutor

    class _Spy(real_pool):
        def __init__(self, max_workers=None, **kwargs):
            seen["max_workers"] = max_workers
            super().__init__(max_workers=max_workers, **kwargs)

    monkeypatch.setattr(shared_dl, "ThreadPoolExecutor", _Spy)

    result = dl.download_images(_Client(), [SCAN], tmp_path, workers=1000)

    assert seen["max_workers"] == dl.MAX_WORKERS
    assert result.ok == 200


def test_the_pool_is_never_larger_than_the_work(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(3))
    seen: dict = {}
    real_pool = shared_dl.ThreadPoolExecutor

    class _Spy(real_pool):
        def __init__(self, max_workers=None, **kwargs):
            seen["max_workers"] = max_workers
            super().__init__(max_workers=max_workers, **kwargs)

    monkeypatch.setattr(shared_dl, "ThreadPoolExecutor", _Spy)

    dl.download_images(_Client(), [SCAN], tmp_path, workers=32)

    assert seen["max_workers"] == 3  # 3 frames, so 3 threads — not 32


def test_no_pool_is_spawned_for_an_empty_experiment(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: [])

    result = dl.download_images(_Client(), [SCAN], tmp_path, workers=8)

    assert result.total == 0


def test_frames_really_are_downloaded_in_parallel(tmp_path, monkeypatch):
    """Catches the downloads quietly going back to one at a time."""
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(8))
    barrier = threading.Barrier(4, timeout=5)

    class _SlowBucket:
        def download(self, object_path):
            barrier.wait()  # only passes if >=4 threads are in flight together
            return f"bytes::{object_path}".encode()

    client = _Client()
    client.bucket = _SlowBucket()

    started = time.monotonic()
    result = dl.download_images(client, [SCAN], tmp_path, workers=4)

    assert result.ok == 8  # a serial run would deadlock and time out on the barrier
    assert time.monotonic() - started < 5


# --- failure isolation ------------------------------------------------------


def test_a_scan_whose_frame_list_fails_is_recorded_not_raised(tmp_path, monkeypatch):
    def _fetch_images(client, scan_id):
        if scan_id == 2:
            raise RuntimeError("listing blew up")
        return _images(2)

    monkeypatch.setattr(dl, "fetch_images", _fetch_images)

    result = dl.download_images(_Client(), [SCAN, SCAN_B], tmp_path, workers=4)

    # An unlisted scan is NOT one failed frame — its frame count is unknown.
    assert result.ok == 2 and result.total == 2
    assert result.failed == 0 and result.scans_unlisted == 1
    assert result.incomplete
    failure = next(f for f in result.frames if f.unlisted)
    assert failure.scan_id == 2 and "list images" in failure.error


def test_one_bad_frame_does_not_abort_a_concurrent_run(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(6))

    class _OneBadBucket:
        def download(self, object_path):
            if object_path.endswith("3.png"):
                raise RuntimeError("500 storage error")
            return f"bytes::{object_path}".encode()

    client = _Client()
    client.bucket = _OneBadBucket()

    result = dl.download_images(client, [SCAN], tmp_path, workers=4)

    assert result.ok == 5 and result.failed == 1
    assert result.frames[3].frame_number == 3 and not result.frames[3].ok


def test_a_failed_frame_leaves_no_temp_file_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(4))

    class _AllBadBucket:
        def download(self, object_path):
            raise RuntimeError("500 storage error")

    client = _Client()
    client.bucket = _AllBadBucket()

    dl.download_images(client, [SCAN], tmp_path, workers=4)

    assert list(tmp_path.rglob(".dl-*")) == []


# --- concurrency + resume ---------------------------------------------------


def test_a_concurrent_run_survives_a_token_refresh(tmp_path, monkeypatch):
    """Each thread needs its own bucket handle, so a renewal mid-run is harmless."""
    from test_download_session_resume import _RotatingClient

    client = _RotatingClient()
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(20))
    original = client.storage.from_

    def _refresh_partway(name):
        if client.handles_issued == 5:
            client.auto_refresh()
        return original(name)

    client.storage.from_ = _refresh_partway

    result = dl.download_images(client, [SCAN], tmp_path, workers=4)

    assert result.ok == 20 and result.failed == 0


def test_resume_skips_existing_frames_under_concurrency(tmp_path, monkeypatch):
    client = _Client()
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(10))
    for number in (2, 5, 7):
        existing = _frame(tmp_path, number)
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_bytes(b"from-an-earlier-run")

    result = dl.download_images(client, [SCAN], tmp_path, workers=8)

    assert result.skipped == 3 and result.downloaded == 7
    assert client.bucket.calls == 7
    assert _frame(tmp_path, 5).read_bytes() == b"from-an-earlier-run"



# --- CLI --------------------------------------------------------------------


def _patch_cli(monkeypatch, client):
    monkeypatch.setattr("bloomctl.credentials.load_credentials", lambda *a, **k: CREDS)
    monkeypatch.setattr(auth, "make_authed_client", lambda creds: client)
    monkeypatch.setattr(dl, "fetch_scans", lambda *a, **k: [SCAN])
    monkeypatch.setattr(dl, "fetch_genotypes", lambda c, ids: {42: "Spring-32"})
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(3))


def test_cli_workers_flag_reaches_download_images(tmp_path, monkeypatch):
    _patch_cli(monkeypatch, _Client())
    seen: dict = {}
    real = dl.download_images

    def _spy(client, scans, out_dir, **kwargs):
        seen.update(kwargs)
        return real(client, scans, out_dir, **kwargs)

    monkeypatch.setattr(dl, "download_images", _spy)

    result = CliRunner().invoke(
        cli, ["cyl", "download", str(tmp_path / "out"), "--experiment-id", "17957", "--workers", "3"]
    )

    assert result.exit_code == 0, result.output
    assert seen["workers"] == 3


def test_cli_defaults_to_the_documented_worker_count(tmp_path, monkeypatch):
    _patch_cli(monkeypatch, _Client())
    seen: dict = {}
    real = dl.download_images

    def _spy(client, scans, out_dir, **kwargs):
        seen.update(kwargs)
        return real(client, scans, out_dir, **kwargs)

    monkeypatch.setattr(dl, "download_images", _spy)

    result = CliRunner().invoke(
        cli, ["cyl", "download", str(tmp_path / "out"), "--experiment-id", "17957"]
    )

    assert result.exit_code == 0, result.output
    assert seen["workers"] == dl.DEFAULT_WORKERS


def test_cli_rejects_an_out_of_range_worker_count(tmp_path, monkeypatch):
    _patch_cli(monkeypatch, _Client())

    for bad in ("0", "65", "-2"):
        result = CliRunner().invoke(
            cli,
            ["cyl", "download", str(tmp_path / "out"), "--experiment-id", "17957", "-n", bad],
        )
        assert result.exit_code != 0, f"--workers {bad} should be rejected"
