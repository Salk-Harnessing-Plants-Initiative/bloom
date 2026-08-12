"""`bloomctl.cyl._locks` — shared file-based lock/lease primitive (bloom #653/#481)."""

import json
import os

import pytest

import bloomctl.cyl._locks as locks


def test_acquire_creates_lock_file_with_pid_and_acquired_at(tmp_path):
    lock_path = tmp_path / "scan_1.lock"
    with locks.acquire_lock(lock_path, staleness_seconds=900):
        assert lock_path.exists()
        body = json.loads(lock_path.read_text(encoding="utf-8"))
        assert body["pid"] == os.getpid()
        assert isinstance(body["acquired_at"], (int, float))


def test_lock_file_removed_on_normal_exit(tmp_path):
    lock_path = tmp_path / "scan_1.lock"
    with locks.acquire_lock(lock_path, staleness_seconds=900):
        pass
    assert not lock_path.exists()


def test_lock_file_removed_even_when_guarded_code_raises(tmp_path):
    lock_path = tmp_path / "scan_1.lock"
    with pytest.raises(ValueError):
        with locks.acquire_lock(lock_path, staleness_seconds=900):
            raise ValueError("simulated failure inside guarded section")
    assert not lock_path.exists()


def test_acquiring_a_held_non_stale_lock_raises_contended_naming_pid_and_age(tmp_path):
    lock_path = tmp_path / "scan_1.lock"
    lock_path.write_text(
        json.dumps({"pid": 12345, "acquired_at": locks.time.time()}), encoding="utf-8"
    )

    with pytest.raises(locks.LockContendedError) as exc_info:
        with locks.acquire_lock(lock_path, staleness_seconds=900):
            pass

    assert "12345" in str(exc_info.value)
    assert lock_path.read_text(encoding="utf-8")  # untouched, still there


def test_acquiring_a_lock_older_than_threshold_reclaims_it(tmp_path):
    lock_path = tmp_path / "scan_1.lock"
    stale_acquired_at = locks.time.time() - 1000
    lock_path.write_text(
        json.dumps({"pid": 99999, "acquired_at": stale_acquired_at}), encoding="utf-8"
    )

    with locks.acquire_lock(lock_path, staleness_seconds=900):
        body = json.loads(lock_path.read_text(encoding="utf-8"))
        assert body["pid"] == os.getpid()
        assert body["acquired_at"] != stale_acquired_at


def test_lock_aged_exactly_at_threshold_is_not_stale(tmp_path, monkeypatch):
    """Pins the boundary operator (`>`, not `>=`) deterministically via a fixed clock —
    real wall-clock time can't reliably hit an *exact* age without racing."""
    lock_path = tmp_path / "scan_1.lock"
    fixed_now = 1_000_000.0
    monkeypatch.setattr(locks.time, "time", lambda: fixed_now)

    staleness_seconds = 900
    lock_path.write_text(
        json.dumps({"pid": 111, "acquired_at": fixed_now - staleness_seconds}),
        encoding="utf-8",
    )

    with pytest.raises(locks.LockContendedError):
        with locks.acquire_lock(lock_path, staleness_seconds=staleness_seconds):
            pass


def test_two_different_paths_never_contend(tmp_path):
    path_a = tmp_path / "scan_1.lock"
    path_b = tmp_path / "scan_2.lock"

    with locks.acquire_lock(path_a, staleness_seconds=900):
        with locks.acquire_lock(path_b, staleness_seconds=900):
            assert path_a.exists()
            assert path_b.exists()


def test_lock_fd_is_closed_before_guarded_code_runs(tmp_path, monkeypatch):
    """Windows blocks deleting a file while any handle to it is open (no
    FILE_SHARE_DELETE) — this repo's CI is Linux-only, where unlinking an open file is
    always fine, so an end-to-end deletion test wouldn't catch a leaked fd. Asserting the
    close call happens before the guarded code runs is what actually verifies it."""
    events = []
    real_close = os.close

    def _tracking_close(fd):
        events.append("close")
        real_close(fd)

    monkeypatch.setattr(os, "close", _tracking_close)

    lock_path = tmp_path / "scan_1.lock"
    with locks.acquire_lock(lock_path, staleness_seconds=900):
        events.append("guarded_code_ran")

    assert events == ["close", "guarded_code_ran"]


def test_acquire_creates_missing_parent_directory(tmp_path):
    lock_path = tmp_path / ".locks" / "scan_1.lock"
    assert not lock_path.parent.exists()

    with locks.acquire_lock(lock_path, staleness_seconds=900):
        assert lock_path.exists()


def test_reclaim_rereads_before_unlink_and_backs_off_if_content_changed(tmp_path, monkeypatch):
    """Proves the re-read-before-unlink mitigation: if another process has already
    reclaimed/re-acquired the lock between our staleness check and our unlink attempt, we
    must back off with LockContendedError instead of deleting its live lock."""
    lock_path = tmp_path / "scan_1.lock"
    stale_acquired_at = locks.time.time() - 1000
    lock_path.write_text(
        json.dumps({"pid": 1, "acquired_at": stale_acquired_at}), encoding="utf-8"
    )

    real_read_lock_info = locks._read_lock_info
    call_count = {"n": 0}

    def _flaky_read(path):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return real_read_lock_info(path)
        # simulate a peer having reclaimed and re-acquired between our two reads
        return {"pid": 2, "acquired_at": locks.time.time()}

    monkeypatch.setattr(locks, "_read_lock_info", _flaky_read)

    with pytest.raises(locks.LockContendedError):
        with locks.acquire_lock(lock_path, staleness_seconds=900):
            pass


def test_reclaim_of_an_already_removed_lock_raises_contended_not_file_not_found(
    tmp_path, monkeypatch
):
    lock_path = tmp_path / "scan_1.lock"
    stale_acquired_at = locks.time.time() - 1000
    lock_path.write_text(
        json.dumps({"pid": 1, "acquired_at": stale_acquired_at}), encoding="utf-8"
    )

    real_read_lock_info = locks._read_lock_info

    def _read_then_vanish(path):
        return real_read_lock_info(path)

    real_unlink = os.unlink

    def _unlink_raises_missing(path):
        raise FileNotFoundError(f"simulated: {path} already removed by a peer")

    monkeypatch.setattr(locks, "_read_lock_info", _read_then_vanish)
    monkeypatch.setattr(os, "unlink", _unlink_raises_missing)

    with pytest.raises(locks.LockContendedError):
        with locks.acquire_lock(lock_path, staleness_seconds=900):
            pass

    monkeypatch.setattr(os, "unlink", real_unlink)
