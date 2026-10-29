"""Unit tests for the video queues' wrappers and the claim loop both workers run
(a recording RPC stub — no DB), matching test_dispatch_worker.py's convention."""

import pytest

import video_queue as vq
from video_writer import ENCODE_TIMEOUT_SECONDS


class _Rpc:
    def __init__(self, data):
        self._data = data

    def execute(self):
        return type("R", (), {"data": self._data})()


class FakeClient:
    """Answers each wrapper from `answers` and records every call."""

    def __init__(self, **answers):
        self.answers = answers
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        answer = self.answers.get(name)
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            answer = answer()
        return _Rpc(answer)

    def names(self):
        return [name for name, _ in self.calls]


JOB = {"job_id": "job-1", "msg_id": 7, "reads": 1, "scan_id": 5, "experiment_id": 1}
Q = vq.CYL_QUEUE


@pytest.fixture(autouse=True)
def _heartbeat(tmp_path, monkeypatch):
    monkeypatch.setattr(vq, "HEARTBEAT_PATH", tmp_path / "beat")


def _queued(*jobs):
    """A claim that hands out `jobs` in order, then nothing."""
    pending = list(jobs)
    return lambda: [pending.pop(0)] if pending else []


def _settled(**extra):
    return {Q.complete: True, Q.fail: True, Q.progress: True, **extra}


# --- constants ---------------------------------------------------------------


def test_the_visibility_timeout_outlives_the_encode_plus_the_upload():
    """Encoding reports nothing while it runs, so the timeout must outlast it."""
    assert (
        vq.VISIBILITY_TIMEOUT_SECONDS
        == int(ENCODE_TIMEOUT_SECONDS) + vq.UPLOAD_MARGIN_SECONDS
    )
    assert vq.VISIBILITY_TIMEOUT_SECONDS > ENCODE_TIMEOUT_SECONDS


def test_the_visibility_timeout_is_inside_what_the_wrappers_accept():
    """The claim and progress wrappers refuse anything past an hour."""
    assert 0 < vq.VISIBILITY_TIMEOUT_SECONDS <= 3600


def test_the_heartbeat_outlives_the_encode():
    assert vq.HEARTBEAT_STALE_SECONDS > vq.VISIBILITY_TIMEOUT_SECONDS


def test_the_stage_names_are_the_ones_the_web_pins():
    assert (vq.STAGE_DOWNLOADING, vq.STAGE_ENCODING, vq.STAGE_UPLOADING) == (
        "downloading",
        "encoding",
        "uploading",
    )


@pytest.mark.parametrize("queue", [vq.PLATE_QUEUE, vq.CYL_QUEUE], ids=lambda q: q.name)
def test_each_queue_names_its_own_five_wrappers(queue):
    names = [queue.enqueue, queue.claim, queue.progress, queue.complete, queue.fail]
    assert len(set(names)) == 5
    kind = "gravi_plate_video" if queue is vq.PLATE_QUEUE else "cyl_video"
    assert all(kind in name for name in names)


# --- wrappers ----------------------------------------------------------------


def test_enqueue_answers_the_job_and_whether_it_was_new():
    client = FakeClient(**{Q.enqueue: [{"job_id": "job-1", "created": False}]})
    assert vq.enqueue(client, Q, {"p_scan_id": 5}) == {
        "job_id": "job-1",
        "created": False,
    }
    assert client.calls == [(Q.enqueue, {"p_scan_id": 5})]


def test_an_enqueue_that_returns_no_job_is_an_error():
    with pytest.raises(RuntimeError):
        vq.enqueue(FakeClient(**{Q.enqueue: []}), Q, {})


def test_claim_passes_the_visibility_timeout_and_delivery_limit():
    client = FakeClient(**{Q.claim: [JOB]})
    assert vq.claim(client, Q) == JOB
    assert client.calls == [
        (Q.claim, {"p_vt": vq.VISIBILITY_TIMEOUT_SECONDS, "p_max_reads": vq.MAX_READS})
    ]


def test_an_empty_claim_is_none():
    assert vq.claim(FakeClient(**{Q.claim: []}), Q) is None


def test_a_progress_report_renews_with_the_same_timeout_the_claim_took():
    """A shorter renewal would move visibility backwards and hand the job out again."""
    client = FakeClient(**{Q.progress: True})
    assert vq.report(client, Q, JOB, "encoding", 3, 9) is True
    assert client.calls == [
        (
            Q.progress,
            {
                "p_job_id": "job-1",
                "p_msg_id": 7,
                "p_stage": "encoding",
                "p_frames_done": 3,
                "p_frames_total": 9,
                "p_vt": vq.VISIBILITY_TIMEOUT_SECONDS,
            },
        )
    ]


def test_complete_and_fail_settle_the_claimed_message():
    client = FakeClient(**{Q.complete: True, Q.fail: True})
    vq.complete(client, Q, JOB, "kept")
    vq.fail(client, Q, JOB, "no_images", "No images found for scan 5")
    assert client.calls == [
        (Q.complete, {"p_job_id": "job-1", "p_msg_id": 7, "p_outcome": "kept"}),
        (
            Q.fail,
            {
                "p_job_id": "job-1",
                "p_msg_id": 7,
                "p_error_code": "no_images",
                "p_error": "No images found for scan 5",
            },
        ),
    ]


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

    return vq.Progress(send, clock=clock, interval=1.0)


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
    assert vq.HEARTBEAT_PATH.exists()


# --- the claim loop ----------------------------------------------------------


def _worker(render, client):
    return vq.Worker(Q, render, lambda: client, sleep=lambda s: None)


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
    monkeypatch.setattr(vq.signal, "signal", lambda *a: None)
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
    monkeypatch.setattr(vq.signal, "signal", lambda *a: None)
    worker.run()

    assert client.names() == [Q.claim, Q.complete], "a job was claimed after the stop"


def test_the_loop_reconnects_after_a_claim_error(monkeypatch):
    broken = FakeClient(**{Q.claim: RuntimeError("JWT expired")})
    fresh = FakeClient(**{Q.claim: []})
    clients = [broken, fresh]
    worker = vq.Worker(
        Q, lambda *a: "rendered", lambda: clients.pop(0), sleep=lambda s: None
    )
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            worker.stop()

    worker.sleep = sleep
    monkeypatch.setattr(vq.signal, "signal", lambda *a: None)
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

    worker = vq.Worker(Q, lambda *a: "rendered", connect, sleep=lambda s: None)
    worker.sleep = lambda s: worker.stop() if client.calls else None
    monkeypatch.setattr(vq.signal, "signal", lambda *a: None)
    worker.run()

    assert len(attempts) == 2
    assert client.names() == [Q.claim]


def test_sigterm_and_sigint_both_stop_the_loop(monkeypatch):
    installed = {}
    monkeypatch.setattr(
        vq.signal, "signal", lambda sig, handler: installed.update({sig: handler})
    )
    worker = vq.Worker(Q, lambda *a: "rendered", lambda: None, sleep=lambda s: None)
    worker.running = False
    worker.run()
    assert set(installed) == {vq.signal.SIGTERM, vq.signal.SIGINT}
    assert all(handler == worker.stop for handler in installed.values())


# --- stall watchdog ----------------------------------------------------------


def test_a_stall_is_far_longer_than_the_silent_encode():
    """Only a hang may trip it: encoding reports nothing for up to its own timeout."""
    assert (
        vq.RENDER_STALL_SECONDS > vq.VISIBILITY_TIMEOUT_SECONDS > ENCODE_TIMEOUT_SECONDS
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

    watchdog = vq.Watchdog(
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
    watchdog = vq.Watchdog(
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

    worker = vq.Worker(
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
    assert failed and failed[0]["p_error_code"] == vq.STALLED
    assert codes == [1]


def test_a_render_that_finishes_leaves_the_worker_running():
    client = FakeClient(**_settled(**{Q.claim: [JOB]}))
    codes = []
    worker = vq.Worker(
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
