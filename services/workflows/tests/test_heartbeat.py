"""The liveness heartbeat, and the probe the container healthcheck runs.

dispatch_worker and status_poller have no HTTP server, so before this the only
signal was "the process exists" — which a loop wedged on a hung socket also
satisfies. These pin the part that distinguishes the two: the timestamp goes
stale when the loop stops turning.
"""
from __future__ import annotations

import time

import heartbeat


def test_touch_then_the_heartbeat_is_fresh(tmp_path):
    hb = tmp_path / "heartbeat"
    heartbeat.touch(hb)
    assert hb.exists()
    assert heartbeat.is_fresh(max_age=60, path=hb)


def test_no_heartbeat_is_not_fresh(tmp_path):
    """A loop that has never been round once is not healthy."""
    hb = tmp_path / "heartbeat"
    assert heartbeat.age_seconds(hb) is None
    assert not heartbeat.is_fresh(max_age=60, path=hb)


def test_a_stale_heartbeat_is_not_fresh(tmp_path):
    """The wedged-loop case: the process is alive, the loop is not turning."""
    hb = tmp_path / "heartbeat"
    heartbeat.touch(hb)
    later = time.time() + 600
    assert heartbeat.age_seconds(hb, now=later) > 599
    assert not heartbeat.is_fresh(max_age=90, path=hb, now=later)


def test_the_age_boundary_is_inclusive(tmp_path):
    """Exactly at max_age still counts as alive, so a probe landing on the
    boundary doesn't flap."""
    hb = tmp_path / "heartbeat"
    heartbeat.touch(hb)
    at_limit = hb.stat().st_mtime + 90
    assert heartbeat.is_fresh(max_age=90, path=hb, now=at_limit)
    assert not heartbeat.is_fresh(max_age=90, path=hb, now=at_limit + 0.001)


def test_touch_creates_the_parent_directory(tmp_path):
    hb = tmp_path / "nested" / "dir" / "heartbeat"
    heartbeat.touch(hb)
    assert hb.exists()


def test_touch_never_raises_when_the_path_is_unwritable(tmp_path):
    """A worker must not die because its liveness file could not be written —
    a write that keeps failing surfaces as a stale heartbeat instead."""
    blocker = tmp_path / "a-file"
    blocker.write_text("not a directory", encoding="utf-8")
    heartbeat.touch(blocker / "heartbeat")  # parent is a file, not a dir


def test_touch_advances_the_timestamp(tmp_path):
    hb = tmp_path / "heartbeat"
    heartbeat.touch(hb)
    first = hb.stat().st_mtime
    time.sleep(0.01)
    heartbeat.touch(hb)
    assert hb.stat().st_mtime >= first


# --------------------------------------------------------------------------- #
# The probe, as the container healthcheck invokes it.
# --------------------------------------------------------------------------- #

def test_probe_exits_zero_on_a_fresh_heartbeat(tmp_path):
    hb = tmp_path / "heartbeat"
    heartbeat.touch(hb)
    assert heartbeat.main(["--max-age", "90", "--path", str(hb)]) == 0


def test_probe_exits_nonzero_when_there_is_no_heartbeat(tmp_path):
    hb = tmp_path / "heartbeat"
    assert heartbeat.main(["--max-age", "90", "--path", str(hb)]) == 1


def test_probe_exits_nonzero_on_a_stale_heartbeat(tmp_path):
    """Written far enough in the past that any max-age of 1s is exceeded."""
    hb = tmp_path / "heartbeat"
    heartbeat.touch(hb)
    import os

    old = time.time() - 3600
    os.utime(hb, (old, old))
    assert heartbeat.main(["--max-age", "1", "--path", str(hb)]) == 1


def test_probe_reports_the_age_it_measured(tmp_path, capsys):
    hb = tmp_path / "heartbeat"
    heartbeat.touch(hb)
    heartbeat.main(["--max-age", "90", "--path", str(hb)])
    assert "heartbeat" in capsys.readouterr().out
