"""Tests for the host-wide run lock.

The property that matters: a second run cannot start while a seed is going,
and it finds out cheaply rather than by corrupting the ledger. The
cross-process cases are exercised with a real subprocess, because the whole
point of flock over a threading.Lock is that it spans processes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import runlock
from runlock import LOCK_FILENAME, LockHeld, LockHolder, RunLock, _format_elapsed

HOLDER_SCRIPT = """
import sys, time
sys.path.insert(0, {module_dir!r})
from runlock import RunLock
lock = RunLock({state_dir!r}).acquire()
print("held", flush=True)
time.sleep(60)
"""


@pytest.fixture
def holder(tmp_path: Path):
    """A separate process holding the lock, killed when the test ends."""
    script = HOLDER_SCRIPT.format(
        module_dir=str(Path(__file__).parent), state_dir=str(tmp_path)
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(script)],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "held"
    yield proc
    proc.kill()
    proc.wait(timeout=10)


class TestSingleProcess:
    def test_acquires_on_a_fresh_state_dir(self, tmp_path: Path):
        lock = RunLock(tmp_path).acquire()
        assert (tmp_path / LOCK_FILENAME).exists()
        lock.release()

    def test_releasing_lets_the_next_run_in(self, tmp_path: Path):
        RunLock(tmp_path).acquire().release()
        RunLock(tmp_path).acquire().release()

    def test_release_is_idempotent(self, tmp_path: Path):
        lock = RunLock(tmp_path).acquire()
        lock.release()
        lock.release()

    def test_context_manager_releases_on_exit(self, tmp_path: Path):
        with RunLock(tmp_path):
            pass
        RunLock(tmp_path).acquire().release()

    def test_context_manager_releases_when_the_body_raises(self, tmp_path: Path):
        with pytest.raises(ValueError):
            with RunLock(tmp_path):
                raise ValueError("boom")
        RunLock(tmp_path).acquire().release()

    def test_records_its_own_pid_while_held(self, tmp_path: Path):
        lock = RunLock(tmp_path).acquire()
        data = json.loads((tmp_path / LOCK_FILENAME).read_text())
        assert data["pid"] == os.getpid()
        lock.release()


class TestCrossProcess:
    def test_a_second_process_is_refused(self, tmp_path: Path, holder):
        with pytest.raises(LockHeld):
            RunLock(tmp_path).acquire()

    def test_the_refusal_names_the_holder(self, tmp_path: Path, holder):
        with pytest.raises(LockHeld) as caught:
            RunLock(tmp_path).acquire()
        assert caught.value.holder.pid == holder.pid
        assert "pid" in caught.value.holder.describe()

    def test_the_lock_frees_when_the_holder_dies(self, tmp_path: Path, holder):
        holder.kill()
        holder.wait(timeout=10)
        # The kernel drops a flock with the process; no stale-pidfile cleanup.
        RunLock(tmp_path).acquire().release()


class TestHolderDescription:
    def test_survives_an_unreadable_lock_file(self, tmp_path: Path):
        (tmp_path / LOCK_FILENAME).write_text("not json at all")
        lock = RunLock(tmp_path)
        # Garbage metadata must not stop a run that can legitimately take it.
        lock.acquire().release()

    def test_describes_an_unknown_holder_without_crashing(self):
        assert "unidentified" in LockHolder(None, None, None).describe()

    def test_describes_a_known_holder(self):
        import time

        text = LockHolder(
            pid=123, started_at=time.time() - 7200, argv="x.py"
        ).describe()
        assert "pid 123" in text
        assert "2h" in text


class TestTheContainerIsRecorded:
    """Inside a container the pid means nothing on the host; the container is
    what a person looks up and stops."""

    def test_records_the_container_it_runs_in(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(runlock, "_container_name", lambda: "3f2a9c1b7d4e")
        lock = RunLock(tmp_path).acquire()
        data = json.loads((tmp_path / LOCK_FILENAME).read_text())
        lock.release()
        assert data["container"] == "3f2a9c1b7d4e"

    def test_outside_a_container_records_none(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(runlock, "_container_name", lambda: "")
        lock = RunLock(tmp_path).acquire()
        data = json.loads((tmp_path / LOCK_FILENAME).read_text())
        lock.release()
        assert data["container"] == ""

    def test_a_recorded_container_is_read_back(self, tmp_path: Path):
        path = tmp_path / LOCK_FILENAME
        path.write_text(json.dumps({"pid": 7, "container": "3f2a9c1b7d4e"}))
        fd = os.open(path, os.O_RDONLY)
        try:
            holder = runlock._read_holder(fd)
        finally:
            os.close(fd)
        assert holder.container == "3f2a9c1b7d4e"

    def test_the_description_leads_with_the_container(self):
        text = LockHolder(
            pid=7, started_at=None, argv=None, container="3f2a9c1b7d4e"
        ).describe()
        assert text.startswith("container 3f2a9c1b7d4e")
        assert "pid 7 inside it" in text

    def test_without_a_container_it_leads_with_the_pid(self):
        assert (
            LockHolder(pid=123, started_at=None, argv=None)
            .describe()
            .startswith("pid 123")
        )

    def test_the_marker_is_the_one_docker_creates(self):
        assert runlock.DOCKER_MARKER == Path("/.dockerenv")

    def test_a_container_is_detected_by_docker_s_marker_file(
        self, tmp_path: Path, monkeypatch
    ):
        marker = tmp_path / ".dockerenv"
        monkeypatch.setattr(runlock, "DOCKER_MARKER", marker)
        monkeypatch.setattr(runlock.socket, "gethostname", lambda: "3f2a9c1b7d4e")
        assert runlock._container_name() == ""
        marker.touch()
        assert runlock._container_name() == "3f2a9c1b7d4e"


class TestElapsedFormatting:
    @pytest.mark.parametrize(
        "seconds,expected",
        [(0, "0m"), (90, "1m"), (3600, "1h 0m"), (7380, "2h 3m"), (180000, "2d 2h")],
    )
    def test_reads_naturally_at_every_scale(self, seconds, expected):
        assert _format_elapsed(seconds) == expected
