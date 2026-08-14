"""Unit tests for the dispatch worker loop (mocks the claim/submit/complete/
fail seam — no real DB, K8s, or httpx needed), matching PR #469's
`test_worker.py` convention."""

import pytest

import dispatch_worker as worker
from k8s_client import K8sConfigError, K8sSubmissionError

_BATCH = {"run_id": 1, "batch_index": 0, "scan_ids": [5, 6], "msg_id": 9}


@pytest.fixture(autouse=True)
def _reset_running():
    worker._running = True
    yield
    worker._running = True


def test_process_one_returns_false_on_empty_queue(monkeypatch):
    monkeypatch.setattr(worker, "claim_batch", lambda c: None)
    assert worker.process_one(object()) is False


def test_process_one_treats_a_dead_lettered_claim_like_an_empty_one(monkeypatch):
    # A poison-message claim dead-lettered by claim_cyl_pipeline_batch itself
    # returns nothing — indistinguishable, at this layer, from an empty queue.
    monkeypatch.setattr(worker, "claim_batch", lambda c: None)
    called = {"submit": False}
    monkeypatch.setattr(
        worker, "submit_workflow", lambda *a, **k: called.update(submit=True)
    )
    assert worker.process_one(object()) is False
    assert called["submit"] is False


def test_process_one_submits_and_completes_on_success(monkeypatch):
    calls = {}
    monkeypatch.setattr(worker, "claim_batch", lambda c: dict(_BATCH))
    monkeypatch.setattr(worker, "build_workflow_body", lambda *a: {"body": True})
    monkeypatch.setattr(worker, "submit_workflow", lambda body: "wf-abc")
    monkeypatch.setattr(
        worker,
        "complete_batch",
        lambda c, r, b, m, s, name: calls.update(complete=(r, b, m, s, name)),
    )
    monkeypatch.setattr(worker, "fail_batch", lambda *a: calls.update(fail=a))

    assert worker.process_one(object()) is True
    assert calls["complete"] == (1, 0, 9, [5, 6], "wf-abc")
    assert "fail" not in calls


def test_process_one_fails_batch_on_k8ssubmissionerror(monkeypatch):
    calls = {}
    monkeypatch.setattr(worker, "claim_batch", lambda c: dict(_BATCH))
    monkeypatch.setattr(worker, "build_workflow_body", lambda *a: {})

    def boom(body):
        raise K8sSubmissionError("Argo Workflow submission failed")

    monkeypatch.setattr(worker, "submit_workflow", boom)
    monkeypatch.setattr(
        worker,
        "fail_batch",
        lambda c, r, b, m, s, err: calls.update(fail=(r, b, m, s, err)),
    )
    monkeypatch.setattr(worker, "complete_batch", lambda *a: calls.update(complete=a))

    assert worker.process_one(object()) is True
    assert calls["fail"][:4] == (1, 0, 9, [5, 6])
    assert "Argo Workflow submission failed" in calls["fail"][4]
    assert "complete" not in calls


def test_process_one_does_not_fail_batch_on_k8sconfigerror(monkeypatch):
    calls = {}
    monkeypatch.setattr(worker, "claim_batch", lambda c: dict(_BATCH))
    monkeypatch.setattr(worker, "build_workflow_body", lambda *a: {})

    def boom(body):
        raise K8sConfigError("missing WORKFLOWS_K8S_TOKEN")

    monkeypatch.setattr(worker, "submit_workflow", boom)
    monkeypatch.setattr(worker, "complete_batch", lambda *a: calls.update(complete=a))
    monkeypatch.setattr(worker, "fail_batch", lambda *a: calls.update(fail=a))

    assert worker.process_one(object()) is True
    assert "complete" not in calls
    assert "fail" not in calls, (
        "a config error must leave the claim unsettled — reclaimable once "
        "fixed, not permanently failed over a deploy/ops mistake"
    )


def test_process_one_does_not_fail_after_completion_error(monkeypatch):
    calls = {}
    monkeypatch.setattr(worker, "claim_batch", lambda c: dict(_BATCH))
    monkeypatch.setattr(worker, "build_workflow_body", lambda *a: {})
    monkeypatch.setattr(worker, "submit_workflow", lambda body: "wf-abc")

    def complete_boom(*a):
        raise RuntimeError("connection reset after commit")

    monkeypatch.setattr(worker, "complete_batch", complete_boom)
    monkeypatch.setattr(worker, "fail_batch", lambda *a: calls.update(fail=a))

    assert worker.process_one(object()) is True
    assert "fail" not in calls  # a submitted workflow is never marked failed


def test_run_sleeps_the_poll_interval_after_an_empty_claim(monkeypatch):
    monkeypatch.setattr(worker, "app_client", lambda: object())
    monkeypatch.setattr(worker, "claim_batch", lambda c: None)
    sleeps = []

    def fake_sleep(secs):
        sleeps.append(secs)
        worker._running = False

    monkeypatch.setattr(worker.time, "sleep", fake_sleep)
    worker.run()
    assert sleeps == [worker.POLL_INTERVAL]


def test_run_loop_reconnects_on_unexpected_error(monkeypatch):
    calls = {"app_client": 0}

    def fake_app_client():
        calls["app_client"] += 1
        return object()

    monkeypatch.setattr(worker, "app_client", fake_app_client)

    attempts = {"n": 0}

    def fake_process_one(client):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("boom")
        worker._running = False
        return False

    monkeypatch.setattr(worker, "process_one", fake_process_one)
    monkeypatch.setattr(worker.time, "sleep", lambda s: None)
    worker.run()
    assert calls["app_client"] == 2  # initial connect + one reconnect after the error


def test_signal_during_submission_lets_it_finish_before_exiting(monkeypatch):
    calls = []
    monkeypatch.setattr(worker, "claim_batch", lambda c: dict(_BATCH))
    monkeypatch.setattr(worker, "build_workflow_body", lambda *a: {})

    def fake_submit(body):
        # Simulate the OS delivering SIGTERM while this submission is
        # in-flight — the signal handler only flips the running-flag; it
        # does not interrupt process_one, so the batch still settles.
        worker._stop(15, None)
        calls.append("submitted")
        return "wf-abc"

    monkeypatch.setattr(worker, "submit_workflow", fake_submit)
    monkeypatch.setattr(worker, "complete_batch", lambda *a: calls.append("completed"))
    monkeypatch.setattr(worker, "fail_batch", lambda *a: calls.append("failed"))

    assert worker.process_one(object()) is True
    assert calls == ["submitted", "completed"]


def test_signal_while_idle_does_not_start_a_new_claim(monkeypatch):
    monkeypatch.setattr(worker, "app_client", lambda: object())
    worker._running = False  # signal already received before run() starts
    claimed = {"called": False}
    monkeypatch.setattr(worker, "claim_batch", lambda c: claimed.update(called=True))
    worker.run()
    assert claimed["called"] is False


def test_run_retries_startup_connection_until_supabase_is_reachable(monkeypatch):
    """A transient Supabase outage when the container starts must not crash
    the process — Docker's `restart: unless-stopped` would just crash-loop it
    forever. run() should retry app_client() with backoff instead."""
    attempts = {"n": 0}

    def fake_app_client():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("connection refused")
        return object()

    monkeypatch.setattr(worker, "app_client", fake_app_client)
    monkeypatch.setattr(worker.time, "sleep", lambda s: None)

    claim_calls = {"n": 0}

    def fake_claim(c):
        claim_calls["n"] += 1
        worker._running = False  # stop after reaching the main loop once
        return None

    monkeypatch.setattr(worker, "claim_batch", fake_claim)

    worker.run()  # must not raise

    assert attempts["n"] == 3  # two failures, then success
    assert claim_calls["n"] == 1  # reached the main loop once connected


def test_signal_while_waiting_to_connect_exits_cleanly(monkeypatch):
    attempts = {"n": 0}

    def fake_app_client():
        attempts["n"] += 1
        raise RuntimeError("still down")

    monkeypatch.setattr(worker, "app_client", fake_app_client)

    def fake_sleep(secs):
        worker._running = False  # signal arrives during the first retry backoff

    monkeypatch.setattr(worker.time, "sleep", fake_sleep)

    claimed = {"called": False}
    monkeypatch.setattr(worker, "claim_batch", lambda c: claimed.update(called=True))

    worker.run()  # must return cleanly, not hang, not raise

    assert attempts["n"] == 1  # tried once, then stopped retrying after the signal
    assert claimed["called"] is False
