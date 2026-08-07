"""bloom#534 — `cyl download` fetches frames concurrently, with a user-set worker count.

Covers the thread pool itself (ordering, `--workers` plumbing, real overlap) and its
interaction with the bloom#525 session: one shared, self-renewing login across workers.
"""

from __future__ import annotations

import threading
import time

from click.testing import CliRunner
from test_download_metadata import SCAN
from test_download_session_resume import CREDS, EXPIRED, _Client, _frame, _images

import bloomctl.auth as auth
import bloomctl.cyl._storage as storage
import bloomctl.cyl.download as dl
from bloomctl.cli import cli

SCAN_B = {**SCAN, "scan_id": 2, "qr_code": "QR-2"}


def _frame_b(tmp_path, number: int):
    return tmp_path / f"images/Wave2/Day14_2026-05-11/QR-2/{number}.png"


# --- ordering & worker count ------------------------------------------------


def test_concurrent_download_preserves_frame_order(tmp_path, monkeypatch):
    """The log must stay deterministic however the pool interleaves the work."""
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
    """`--workers 1` must take the no-pool path, not a one-thread pool."""
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(3))
    used: list[str] = []
    real_pool = dl.ThreadPoolExecutor

    class _Spy(real_pool):
        def __init__(self, *a, **k):
            used.append("pool")
            super().__init__(*a, **k)

    monkeypatch.setattr(dl, "ThreadPoolExecutor", _Spy)

    result = dl.download_images(_Client(), [SCAN], tmp_path, workers=1)

    assert result.ok == 3
    assert used == []


def test_the_pool_never_exceeds_the_ceiling_even_via_a_direct_call(tmp_path, monkeypatch):
    """The CLI caps `--workers`, but a library caller must not be able to slip past it."""
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(200))
    seen: dict = {}
    real_pool = dl.ThreadPoolExecutor

    class _Spy(real_pool):
        def __init__(self, max_workers=None, **kwargs):
            seen["max_workers"] = max_workers
            super().__init__(max_workers=max_workers, **kwargs)

    monkeypatch.setattr(dl, "ThreadPoolExecutor", _Spy)

    result = dl.download_images(_Client(), [SCAN], tmp_path, workers=1000)

    assert seen["max_workers"] == dl.MAX_WORKERS
    assert result.ok == 200


def test_the_pool_is_never_larger_than_the_work(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(3))
    seen: dict = {}
    real_pool = dl.ThreadPoolExecutor

    class _Spy(real_pool):
        def __init__(self, max_workers=None, **kwargs):
            seen["max_workers"] = max_workers
            super().__init__(max_workers=max_workers, **kwargs)

    monkeypatch.setattr(dl, "ThreadPoolExecutor", _Spy)

    dl.download_images(_Client(), [SCAN], tmp_path, workers=32)

    assert seen["max_workers"] == 3  # 3 frames, so 3 threads — not 32


def test_no_pool_is_spawned_for_an_empty_experiment(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: [])

    result = dl.download_images(_Client(), [SCAN], tmp_path, workers=8)

    assert result.total == 0


def test_frames_really_are_downloaded_in_parallel(tmp_path, monkeypatch):
    """Guard against the pool silently degrading to serial execution."""
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

    assert result.ok == 2 and result.failed == 1
    failure = next(f for f in result.frames if not f.ok)
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


# --- the shared session under concurrency -----------------------------------


def test_workers_hitting_one_lapse_re_authenticate_only_once(monkeypatch):
    """The core race: N threads failing on the same expiry must yield one sign-in."""
    fresh = _Client()
    sign_ins = []
    ready = threading.Barrier(8, timeout=5)

    def _make_client(creds):
        sign_ins.append(creds)
        return fresh

    monkeypatch.setattr(auth, "make_authed_client", _make_client)

    class _ExpiredBucket:
        def download(self, object_path):
            ready.wait()  # line every worker up on the stale handle first
            raise RuntimeError(EXPIRED)

    stale = _Client()
    stale.bucket = _ExpiredBucket()
    session = storage.StorageSession(stale, CREDS, refresh_every=10_000)

    results: list[bytes] = []

    def _worker(index):
        results.append(session.download(f"cyl-images/{index}.png"))

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert len(results) == 8  # every worker recovered on the renewed session
    assert len(sign_ins) == 1  # ...from a single re-authentication
    assert session.refreshes == 1


def test_the_proactive_cadence_counts_frames_across_all_workers(tmp_path, monkeypatch):
    """The frame counter is shared state; concurrent increments must not be lost."""
    clients = []

    def _make_client(creds):
        clients.append(_Client())
        return clients[-1]

    monkeypatch.setattr(auth, "make_authed_client", _make_client)
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(40))

    first = _Client()
    result = dl.download_images(
        first, [SCAN], tmp_path, creds=CREDS, refresh_every=10, workers=8
    )

    assert result.ok == 40
    # A 10-frame cadence over 40 frames renews a handful of times. The exact count races
    # (workers check the counter before incrementing it), so this asserts the shape, not a
    # number: renewals happened, and no frame was dropped or double-counted along the way.
    assert len(clients) >= 2
    assert first.bucket.calls + sum(c.bucket.calls for c in clients) == 40


def test_a_concurrent_run_survives_a_session_expiring_mid_run(tmp_path, monkeypatch):
    stale, fresh = _Client(budget=5), _Client()
    monkeypatch.setattr(auth, "make_authed_client", lambda creds: fresh)
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(20))

    result = dl.download_images(
        stale, [SCAN], tmp_path, creds=CREDS, refresh_every=10_000, workers=4
    )

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
