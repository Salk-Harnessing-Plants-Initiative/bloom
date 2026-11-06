"""Unit tests for the progress reporter's throttle and the stall watchdog."""

import pytest

import video_queue as vq
import video_worker_loop as loop
from video_fakes import JOB, Q, Clock, FakeClient, make_reporter, settled, wait_for
from video_writer import ENCODE_TIMEOUT_SECONDS


@pytest.fixture(autouse=True)
def _heartbeat(tmp_path, monkeypatch):
    monkeypatch.setattr(loop, "HEARTBEAT_PATH", tmp_path / "beat")


# --- progress throttle -------------------------------------------------------


def test_progress_reports_at_most_once_per_interval_within_a_stage():
    clock, sent = Clock(), []
    progress = make_reporter(clock, sent)
    for frame in range(5):
        progress("downloading", frame, 86)
        clock.now += 0.3
    assert sent == [("downloading", 0, 86), ("downloading", 4, 86)]


def test_a_stage_change_is_always_reported():
    clock, sent = Clock(), []
    progress = make_reporter(clock, sent)
    progress("downloading", 86, 86)
    progress("encoding", 86, 86)
    progress("uploading", 86, 86)
    assert [stage for stage, _, _ in sent] == ["downloading", "encoding", "uploading"]


def test_a_failed_report_does_not_stop_the_render(caplog):
    clock, sent = Clock(), []
    progress = make_reporter(clock, sent, answer=RuntimeError("db blip"))
    progress("downloading", 0, 3)
    progress("encoding", 3, 3)
    assert len(sent) == 2
    assert "db blip" in caplog.text


def test_a_refused_report_is_noted_once():
    clock, sent = Clock(), []
    progress = make_reporter(clock, sent, answer=False)
    progress("downloading", 0, 3)
    progress("encoding", 3, 3)
    assert progress.lost is True


def test_a_report_touches_the_heartbeat():
    make_reporter(Clock(), [])("downloading", 0, 3)
    assert loop.HEARTBEAT_PATH.exists()


# --- stall watchdog ----------------------------------------------------------


def test_a_stall_is_far_longer_than_the_silent_encode():
    """Only a hang may trip it: encoding reports nothing for up to its own timeout."""
    assert (
        loop.RENDER_STALL_SECONDS
        > vq.VISIBILITY_TIMEOUT_SECONDS
        > ENCODE_TIMEOUT_SECONDS
    )


def test_a_throttled_report_still_counts_as_activity():
    clock = Clock()
    progress = make_reporter(clock, [])
    progress("downloading", 0, 86)
    clock.now += 0.5
    progress("downloading", 1, 86)
    assert progress.last_activity == 0.5


def test_the_watchdog_fires_once_when_the_render_goes_quiet():
    import threading

    clock, fired = Clock(), []
    done = threading.Event()
    progress = make_reporter(clock, [])
    clock.now = 100.0

    def on_stall(quiet):
        fired.append(quiet)
        done.set()

    watchdog = loop.Watchdog(
        progress, on_stall, stall_seconds=10, check_seconds=0.01, clock=clock
    )
    watchdog.start()
    wait_for(done)
    watchdog.stop()
    assert fired == [100.0]


def test_the_watchdog_stays_quiet_while_the_render_moves():
    import time

    clock, fired = Clock(), []
    progress = make_reporter(clock, [])
    watchdog = loop.Watchdog(
        progress, fired.append, stall_seconds=10, check_seconds=0.01, clock=clock
    )
    watchdog.start()
    for frame in range(20):
        clock.now += 5
        progress("downloading", frame, 20)
        time.sleep(0.005)
    watchdog.stop()
    assert fired == []


def test_a_stalled_job_is_failed_and_the_worker_exits():
    """A thread cannot be interrupted; failing the job and exiting lets Docker restart
    the worker and the queue move on, rather than every later job waiting."""
    import threading

    client = FakeClient(**settled(**{Q.claim: [JOB]}))
    exited = threading.Event()
    codes = []

    def exit(code):
        codes.append(code)
        exited.set()

    def hangs(c, job, progress):
        wait_for(exited)
        return "rendered"

    worker = loop.Worker(
        Q,
        hangs,
        lambda: client,
        sleep=lambda s: None,
        exit=exit,
        stall_seconds=0.05,
        check_seconds=0.01,
    )
    worker.process_one(client)

    failed = [params for name, params in client.calls if name == Q.fail]
    assert failed and failed[0]["p_error_code"] == loop.STALLED
    assert codes == [1]


def test_a_render_that_finishes_leaves_the_worker_running():
    client = FakeClient(**settled(**{Q.claim: [JOB]}))
    codes = []
    worker = loop.Worker(
        Q,
        lambda *a: "rendered",
        lambda: client,
        sleep=lambda s: None,
        exit=codes.append,
        stall_seconds=60,
        check_seconds=0.01,
    )
    worker.process_one(client)
    assert codes == []
    assert Q.fail not in client.names()


def test_the_watchdog_is_stopped_once_the_job_issettled():
    """A watchdog left running would fail and exit the worker long after its job."""
    import time

    client = FakeClient(**settled(**{Q.claim: [JOB]}))
    codes = []
    worker = loop.Worker(
        Q,
        lambda *a: "rendered",
        lambda: client,
        sleep=lambda s: None,
        exit=codes.append,
        stall_seconds=0.05,
        check_seconds=0.01,
    )
    worker.process_one(client)
    time.sleep(0.2)
    assert codes == [], "the watchdog outlived its job"
    assert Q.fail not in client.names()


def test_a_slow_settle_after_the_render_is_never_failed_as_stalled():
    """The watchdog stops when the render returns, so it cannot fail a made video."""
    import time

    def slow_complete():
        time.sleep(0.2)
        return True

    client = FakeClient(**settled(**{Q.claim: [JOB], Q.complete: slow_complete}))
    codes = []
    worker = loop.Worker(
        Q,
        lambda *a: "rendered",
        lambda: client,
        sleep=lambda s: None,
        exit=codes.append,
        stall_seconds=0.05,
        check_seconds=0.01,
    )
    worker.process_one(client)

    assert codes == []
    assert Q.fail not in client.names()
