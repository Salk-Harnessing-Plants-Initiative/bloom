"""Unit tests for the claim loop both video workers run: the progress throttle,
the watchdog and the Worker (a recording RPC stub — no DB)."""

import pytest

import video_progress as vp
import video_queue as vq
import video_worker_loop as loop
from test_video_queue import JOB, Q, FakeClient, _queued, _settled
from video_writer import ENCODE_TIMEOUT_SECONDS


@pytest.fixture(autouse=True)
def _heartbeat(tmp_path, monkeypatch):
    monkeypatch.setattr(loop, "HEARTBEAT_PATH", tmp_path / "beat")


def test_the_stage_names_are_the_ones_the_web_pins():
    assert (vp.STAGE_DOWNLOADING, vp.STAGE_ENCODING, vp.STAGE_UPLOADING) == (
        "downloading",
        "encoding",
        "uploading",
    )


def test_the_heartbeat_outlives_the_encode():
    assert loop.HEARTBEAT_STALE_SECONDS > vq.VISIBILITY_TIMEOUT_SECONDS


# --- progress throttle -------------------------------------------------------


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _progress(clock, sent, answer=True):
    def send(stage, done, total):
        sent.append((stage, done, total))
        if isinstance(answer, Exception):
            raise answer
        return answer

    return loop.ProgressReporter(send, clock=clock, interval=1.0)


def test_progress_reports_at_most_once_per_interval_within_a_stage():
    clock, sent = Clock(), []
    progress = _progress(clock, sent)
    for frame in range(5):
        progress("downloading", frame, 86)
        clock.now += 0.3
    assert sent == [("downloading", 0, 86), ("downloading", 4, 86)]


def test_a_stage_change_is_always_reported():
    clock, sent = Clock(), []
    progress = _progress(clock, sent)
    progress("downloading", 86, 86)
    progress("encoding", 86, 86)
    progress("uploading", 86, 86)
    assert [stage for stage, _, _ in sent] == ["downloading", "encoding", "uploading"]


def test_a_failed_report_does_not_stop_the_render(caplog):
    clock, sent = Clock(), []
    progress = _progress(clock, sent, answer=RuntimeError("db blip"))
    progress("downloading", 0, 3)
    progress("encoding", 3, 3)
    assert len(sent) == 2
    assert "db blip" in caplog.text


def test_a_refused_report_is_noted_once():
    clock, sent = Clock(), []
    progress = _progress(clock, sent, answer=False)
    progress("downloading", 0, 3)
    progress("encoding", 3, 3)
    assert progress.lost is True


def test_a_report_touches_the_heartbeat():
    _progress(Clock(), [])("downloading", 0, 3)
    assert loop.HEARTBEAT_PATH.exists()


# --- the claim loop ----------------------------------------------------------


def _worker(render, client):
    return loop.Worker(Q, render, lambda: client, sleep=lambda s: None)


def test_an_empty_queue_handles_nothing():
    client = FakeClient(**{Q.claim: []})
    assert _worker(lambda *a: "rendered", client).process_one(client) is False
    assert client.names() == [Q.claim]


@pytest.mark.parametrize("outcome", ["rendered", "kept"])
def test_a_finished_render_completes_with_its_outcome(outcome):
    client = FakeClient(**_settled(**{Q.claim: [JOB]}))
    assert _worker(lambda *a: outcome, client).process_one(client) is True
    assert client.calls[-1] == (
        Q.complete,
        {"p_job_id": "job-1", "p_msg_id": 7, "p_outcome": outcome},
    )


def test_the_render_is_handed_the_client_the_job_and_a_reporter():
    client = FakeClient(**_settled(**{Q.claim: [JOB]}))
    seen = {}

    def render(c, job, progress):
        seen.update(client=c, job=job)
        progress("downloading", 1, 2)
        return "rendered"

    _worker(render, client).process_one(client)

    assert seen == {"client": client, "job": JOB}
    assert client.names() == [Q.claim, Q.progress, Q.complete]


def test_a_refusal_fails_the_job_with_its_code():
    client = FakeClient(**_settled(**{Q.claim: [JOB]}))

    def render(*a):
        raise vq.RenderFailed("no_images", "No images found for scan 5")

    _worker(render, client).process_one(client)

    assert client.calls[-1] == (
        Q.fail,
        {
            "p_job_id": "job-1",
            "p_msg_id": 7,
            "p_error_code": "no_images",
            "p_error": "No images found for scan 5",
        },
    )


def test_an_unexpected_error_fails_the_job_naming_its_class():
    client = FakeClient(**_settled(**{Q.claim: [JOB]}))

    def render(*a):
        raise ZeroDivisionError("division by zero")

    _worker(render, client).process_one(client)

    name, params = client.calls[-1]
    assert name == Q.fail
    assert params["p_error_code"] == vq.UNEXPECTED
    assert params["p_error"] == "ZeroDivisionError: division by zero"


@pytest.mark.parametrize("verb", ["complete", "fail"])
def test_a_settle_that_errors_leaves_the_job_for_redelivery(verb, caplog):
    """Guessing would mark a stored video failed, or a failed one done."""
    client = FakeClient(**{Q.claim: [JOB], getattr(Q, verb): RuntimeError("db down")})

    def render(*a):
        if verb == "fail":
            raise vq.RenderFailed("no_images", "none")
        return "rendered"

    assert _worker(render, client).process_one(client) is True
    assert "leaving it for redelivery" in caplog.text


def test_a_job_settled_elsewhere_is_logged_not_raised(caplog):
    client = FakeClient(**{Q.claim: [JOB], Q.complete: False})
    _worker(lambda *a: "rendered", client).process_one(client)
    assert "already settled" in caplog.text


def test_a_claim_error_reaches_the_loop():
    """run() reconnects on it; an expired session surfaces as exactly this."""
    client = FakeClient(**{Q.claim: RuntimeError("JWT expired")})
    with pytest.raises(RuntimeError):
        _worker(lambda *a: "rendered", client).process_one(client)


def test_jobs_are_rendered_one_at_a_time_in_claim_order(monkeypatch):
    second = {**JOB, "job_id": "job-2", "msg_id": 8}
    client = FakeClient(**_settled(**{Q.claim: _queued(JOB, second)}))
    rendered = []
    worker = _worker(
        lambda c, job, p: rendered.append(job["job_id"]) or "rendered", client
    )

    worker.sleep = lambda s: worker.stop()
    monkeypatch.setattr(loop.signal, "signal", lambda *a: None)
    worker.run()

    assert rendered == ["job-1", "job-2"]


def test_a_stop_during_a_render_finishes_it_and_claims_no_more(monkeypatch):
    second = {**JOB, "job_id": "job-2", "msg_id": 8}
    client = FakeClient(**_settled(**{Q.claim: _queued(JOB, second)}))
    worker = _worker(None, client)

    def render(c, job, p):
        worker.stop()
        return "rendered"

    worker.render = render
    monkeypatch.setattr(loop.signal, "signal", lambda *a: None)
    worker.run()

    assert client.names() == [Q.claim, Q.complete], "a job was claimed after the stop"


def test_the_loop_reconnects_after_a_claim_error(monkeypatch):
    broken = FakeClient(**{Q.claim: RuntimeError("JWT expired")})
    fresh = FakeClient(**{Q.claim: []})
    clients = [broken, fresh]
    worker = loop.Worker(
        Q, lambda *a: "rendered", lambda: clients.pop(0), sleep=lambda s: None
    )
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            worker.stop()

    worker.sleep = sleep
    monkeypatch.setattr(loop.signal, "signal", lambda *a: None)
    worker.run()

    assert fresh.names() == [Q.claim], "the loop did not move to a new client"


def test_a_failed_first_connection_is_retried(monkeypatch):
    attempts = []
    client = FakeClient(**{Q.claim: []})

    def connect():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("auth down")
        return client

    worker = loop.Worker(Q, lambda *a: "rendered", connect, sleep=lambda s: None)
    worker.sleep = lambda s: worker.stop() if client.calls else None
    monkeypatch.setattr(loop.signal, "signal", lambda *a: None)
    worker.run()

    assert len(attempts) == 2
    assert client.names() == [Q.claim]


def test_sigterm_and_sigint_both_stop_the_loop(monkeypatch):
    installed = {}
    monkeypatch.setattr(
        loop.signal, "signal", lambda sig, handler: installed.update({sig: handler})
    )
    worker = loop.Worker(Q, lambda *a: "rendered", lambda: None, sleep=lambda s: None)
    worker.running = False
    worker.run()
    assert set(installed) == {loop.signal.SIGTERM, loop.signal.SIGINT}
    assert all(handler == worker.stop for handler in installed.values())


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
    progress = _progress(clock, [])
    progress("downloading", 0, 86)
    clock.now += 0.5
    progress("downloading", 1, 86)
    assert progress.last_activity == 0.5


def _wait_for(event, seconds=2.0):
    assert event.wait(seconds), "the watchdog never answered"


def test_the_watchdog_fires_once_when_the_render_goes_quiet():
    import threading

    clock, fired = Clock(), []
    done = threading.Event()
    progress = _progress(clock, [])
    clock.now = 100.0

    def on_stall(quiet):
        fired.append(quiet)
        done.set()

    watchdog = loop.Watchdog(
        progress, on_stall, stall_seconds=10, check_seconds=0.01, clock=clock
    )
    watchdog.start()
    _wait_for(done)
    watchdog.stop()
    assert fired == [100.0]


def test_the_watchdog_stays_quiet_while_the_render_moves():
    import time

    clock, fired = Clock(), []
    progress = _progress(clock, [])
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

    client = FakeClient(**_settled(**{Q.claim: [JOB]}))
    exited = threading.Event()
    codes = []

    def exit(code):
        codes.append(code)
        exited.set()

    def hangs(c, job, progress):
        _wait_for(exited)
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
    client = FakeClient(**_settled(**{Q.claim: [JOB]}))
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


def test_an_idle_poll_touches_the_heartbeat():
    """An idle worker must still look alive, or the healthcheck finds no file."""
    client = FakeClient(**{Q.claim: []})
    _worker(lambda *a: "rendered", client).process_one(client)
    assert loop.HEARTBEAT_PATH.exists()


def test_waiting_to_connect_touches_the_heartbeat(monkeypatch):
    attempts = []

    def connect():
        attempts.append(1)
        raise RuntimeError("auth down")

    worker = loop.Worker(
        Q, lambda *a: "rendered", connect, sleep=lambda s: worker.stop()
    )
    assert worker._connect() is None
    assert attempts and loop.HEARTBEAT_PATH.exists()


def test_the_watchdog_is_stopped_once_the_job_is_settled():
    """A watchdog left running would fail and exit the worker long after its job."""
    import time

    client = FakeClient(**_settled(**{Q.claim: [JOB]}))
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
