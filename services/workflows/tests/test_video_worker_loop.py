"""Unit tests for the claim loop both video workers run (a recording RPC stub — no DB)."""

import pytest

import video_progress as vp
import video_queue as vq
import video_worker_loop as loop
from video_fakes import JOB, Q, FakeClient, queued, settled


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


# --- the claim loop ----------------------------------------------------------


def _worker(render, client):
    return loop.Worker(Q, render, lambda: client, sleep=lambda s: None)


def test_an_empty_queue_handles_nothing():
    client = FakeClient(**{Q.claim: []})
    assert _worker(lambda *a: "rendered", client).process_one(client) is False
    assert client.names() == [Q.claim]


@pytest.mark.parametrize("outcome", ["rendered", "kept"])
def test_a_finished_render_completes_with_its_outcome(outcome):
    client = FakeClient(**settled(**{Q.claim: [JOB]}))
    assert _worker(lambda *a: outcome, client).process_one(client) is True
    assert client.calls[-1] == (
        Q.complete,
        {"p_job_id": "job-1", "p_msg_id": 7, "p_outcome": outcome},
    )


def test_the_render_is_handed_the_client_the_job_and_a_reporter():
    client = FakeClient(**settled(**{Q.claim: [JOB]}))
    seen = {}

    def render(c, job, progress):
        seen.update(client=c, job=job)
        progress("downloading", 1, 2)
        return "rendered"

    _worker(render, client).process_one(client)

    assert seen == {"client": client, "job": JOB}
    assert client.names() == [Q.claim, Q.progress, Q.complete]


def test_a_refusal_fails_the_job_with_its_code():
    client = FakeClient(**settled(**{Q.claim: [JOB]}))

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
    client = FakeClient(**settled(**{Q.claim: [JOB]}))

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
    client = FakeClient(**settled(**{Q.claim: queued(JOB, second)}))
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
    client = FakeClient(**settled(**{Q.claim: queued(JOB, second)}))
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


# --- backoff and health ------------------------------------------------------


def test_reconnects_wait_longer_each_time_up_to_a_cap():
    waits = [loop.backoff(n) for n in range(10)]
    assert waits[0] == loop.POLL_SECONDS
    assert waits == sorted(waits)
    assert max(waits) == loop.MAX_BACKOFF_SECONDS


def test_waiting_to_connect_backs_off_and_beats_on_every_try(monkeypatch):
    beats, waits = [], []
    monkeypatch.setattr(loop, "beat", lambda: beats.append(1))

    def connect():
        raise RuntimeError("auth down")

    worker = loop.Worker(Q, lambda *a: "rendered", connect)

    def sleep(seconds):
        waits.append(seconds)
        if len(waits) == 4:
            worker.stop()

    worker.sleep = sleep
    assert worker._connect() is None
    assert waits == [loop.backoff(n) for n in range(4)]
    assert len(beats) == 4, "an outage longer than the stale age would read as dead"


def test_the_claim_backoff_starts_over_after_a_good_claim(monkeypatch):
    answers = iter([RuntimeError("blip"), [], RuntimeError("blip")])

    def claim():
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    client = FakeClient(**{Q.claim: claim})
    worker = loop.Worker(Q, lambda *a: "rendered", lambda: client)
    waits = []

    def sleep(seconds):
        waits.append(seconds)
        if len(waits) == 3:
            worker.stop()

    worker.sleep = sleep
    monkeypatch.setattr(loop.signal, "signal", lambda *a: None)
    worker.run()
    assert waits == [loop.backoff(0), loop.POLL_SECONDS, loop.backoff(0)]


def test_the_healthcheck_reads_the_heartbeat_age():
    assert loop.healthy() is False, "no heartbeat yet reads as unhealthy"
    loop.beat()
    stamp = loop.HEARTBEAT_PATH.stat().st_mtime
    assert loop.healthy(now=stamp + loop.HEARTBEAT_STALE_SECONDS) is True
    assert loop.healthy(now=stamp + loop.HEARTBEAT_STALE_SECONDS + 1) is False
