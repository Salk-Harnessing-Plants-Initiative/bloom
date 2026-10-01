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


def test_process_one_swallows_a_failing_fail_batch_call_and_still_returns_true(
    monkeypatch, caplog
):
    """If fail_batch's own RPC errors (transient network/DB issue), that must
    not escape process_one — it would bypass this batch's specific log
    context and hit run()'s generic "loop error, reconnecting" handler
    instead, symmetric with how a failing complete_batch call is handled. The
    fix's actual point (not just "doesn't crash") is that the run_id/
    batch_index context is preserved in a batch-specific log line, not lost
    to a generic handler — assert that log line fires, not just the return
    value, which a regression that kept the try/except but dropped the
    logger.error call (e.g. replaced with a bare pass) would still satisfy."""
    monkeypatch.setattr(worker, "claim_batch", lambda c: dict(_BATCH))
    monkeypatch.setattr(worker, "build_workflow_body", lambda *a: {})

    def boom(body):
        raise K8sSubmissionError("Argo Workflow submission failed")

    monkeypatch.setattr(worker, "submit_workflow", boom)

    def fail_boom(*a):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(worker, "fail_batch", fail_boom)

    with caplog.at_level("ERROR", logger="dispatch_worker"):
        assert worker.process_one(object()) is True

    messages = [r.getMessage() for r in caplog.records]
    assert any(
        "run 1 batch 0" in m and "fail RPC also errored" in m for m in messages
    ), messages


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


def test_process_one_does_not_fail_batch_when_build_workflow_body_raises_k8sconfigerror(
    monkeypatch,
):
    """The sibling test above only makes submit_workflow raise; both calls
    share one try/except block (see process_one), but this PR (bloom #737)
    is what first made build_workflow_body itself capable of raising
    K8sConfigError (a missing/malformed vendored file, a drifted scan-ids
    parameter). Confirms that failure mode gets the identical unsettled-
    claim treatment, not just the pre-existing submit_workflow path — a
    future refactor splitting the two calls into separate try blocks would
    break this silently otherwise."""
    calls = {}
    monkeypatch.setattr(worker, "claim_batch", lambda c: dict(_BATCH))

    def boom(*a):
        raise K8sConfigError("vendored Workflow source is missing")

    monkeypatch.setattr(worker, "build_workflow_body", boom)
    monkeypatch.setattr(
        worker,
        "submit_workflow",
        lambda body: (_ for _ in ()).throw(
            AssertionError(
                "submit_workflow must not be called if build_workflow_body raised"
            )
        ),
    )
    monkeypatch.setattr(worker, "complete_batch", lambda *a: calls.update(complete=a))
    monkeypatch.setattr(worker, "fail_batch", lambda *a: calls.update(fail=a))

    assert worker.process_one(object()) is True
    assert "complete" not in calls
    assert "fail" not in calls, (
        "a config error from build_workflow_body must leave the claim "
        "unsettled, exactly like one from submit_workflow"
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


# --- Refusal: an environment that is switched off or unconfigured (bloom#863) -

_REFUSAL_MESSAGES = {
    "off": "Pipeline dispatch is turned off in this environment",
    "unconfigured": "Pipeline dispatch is not configured in this environment",
}


def _refusing(monkeypatch, reason, detail=""):
    """Wire a claimed batch whose body builder refuses; returns the call log."""
    import k8s_client

    calls = {}
    monkeypatch.setattr(worker, "claim_batch", lambda c: dict(_BATCH))

    def refuse(*a):
        raise k8s_client.K8sDispatchRefusedError(reason, detail)

    monkeypatch.setattr(worker, "build_workflow_body", refuse)
    monkeypatch.setattr(
        worker,
        "submit_workflow",
        lambda body: (_ for _ in ()).throw(
            AssertionError("a refused batch must never be submitted")
        ),
    )
    monkeypatch.setattr(worker, "complete_batch", lambda *a: calls.update(complete=a))
    # A list, so a second fail_batch call can't overwrite the first.
    monkeypatch.setattr(
        worker,
        "fail_batch",
        lambda c, r, b, m, s, err: calls.setdefault("fail", []).append(
            (r, b, m, s, err)
        ),
    )
    return calls


@pytest.mark.parametrize("reason", ["off", "unconfigured"])
def test_process_one_fails_a_refused_batch_at_once_with_a_fixed_message(
    monkeypatch, reason
):
    """Unlike a K8sConfigError, a refusal is settled on this same claim: left
    unsettled it would only be dead-lettered ~5 minutes later as a "poison
    message"."""
    calls = _refusing(monkeypatch, reason)

    assert worker.process_one(object()) is True
    assert calls["fail"] == [(1, 0, 9, [5, 6], _REFUSAL_MESSAGES[reason])]
    assert "complete" not in calls


@pytest.mark.parametrize("reason", ["off", "unconfigured"])
def test_a_refusals_recorded_message_omits_the_detail_its_log_keeps(
    monkeypatch, caplog, reason
):
    """error_message is user-facing: the variable names, paths and secret
    names go to the server log only."""
    detail = (
        "WORKFLOWS_K8S_PIPELINE_HOSTPATH_ROOT has a '.' or '..' segment; "
        "CYL_PIPELINE_TRIGGER_ENABLED; /hpi/../a4_poc bloom_cyl_pipeline "
        "genericsecret-x"
    )
    calls = _refusing(monkeypatch, reason, detail)

    with caplog.at_level("WARNING", logger="dispatch_worker"):
        assert worker.process_one(object()) is True

    [(*_, recorded)] = calls["fail"]
    for leak in (
        "WORKFLOWS_K8S_",
        "CYL_PIPELINE_",
        "/hpi",
        "a4_poc",
        "bloom_cyl_pipeline",
        "genericsecret",
    ):
        assert leak not in recorded
    warnings = [r.getMessage() for r in caplog.records if r.levelname != "DEBUG"]
    # Run, batch, the cause and the detail all reach the log.
    assert any(
        "run 1 batch 0" in m and f"({reason})" in m and detail in m for m in warnings
    ), warnings


def test_a_refused_batch_whose_fail_rpc_errors_is_left_for_redelivery(
    monkeypatch, caplog
):
    calls = _refusing(monkeypatch, "off")

    def fail_boom(*a):
        calls.setdefault("fail_attempts", 0)
        calls["fail_attempts"] += 1
        raise RuntimeError("connection reset")

    monkeypatch.setattr(worker, "fail_batch", fail_boom)

    with caplog.at_level("ERROR", logger="dispatch_worker"):
        assert worker.process_one(object()) is True

    # Tried once, not retried, and the claim is not settled any other way.
    assert calls["fail_attempts"] == 1
    assert "complete" not in calls
    messages = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any("run 1 batch 0" in m and "fail RPC" in m for m in messages), messages


def test_a_switched_off_environment_reaches_neither_argo_nor_the_vendored_file(
    monkeypatch, tmp_path
):
    """With the real body builder (the claim/settle RPCs and the HTTP client
    are stubbed): switched off, the batch is failed with the fixed message, the
    vendored file is never read (it points at a missing file, which would be a
    K8sConfigError and leave the batch unsettled), and no Kubernetes client is
    ever created."""
    import k8s_client

    calls = {}
    monkeypatch.setattr(k8s_client, "PIPELINE_DISPATCH_ENABLED", False)
    monkeypatch.setattr(
        k8s_client, "_VENDORED_WORKFLOW_PATH", tmp_path / "missing.yaml"
    )
    monkeypatch.setattr(
        k8s_client.httpx,
        "Client",
        lambda *a, **k: calls.update(client=True),
    )
    monkeypatch.setattr(worker, "claim_batch", lambda c: dict(_BATCH))
    monkeypatch.setattr(worker, "complete_batch", lambda *a: calls.update(complete=a))
    monkeypatch.setattr(
        worker,
        "fail_batch",
        lambda c, r, b, m, s, err: calls.update(fail=(r, b, m, s, err)),
    )

    assert worker.process_one(object()) is True
    assert calls["fail"] == (1, 0, 9, [5, 6], _REFUSAL_MESSAGES["off"])
    assert "client" not in calls
    assert "complete" not in calls


def test_every_refusal_cause_has_a_message():
    import k8s_client

    assert set(_REFUSAL_MESSAGES) == set(k8s_client.K8sDispatchRefusedError.REASONS)
    assert worker._REFUSAL_MESSAGES == _REFUSAL_MESSAGES
