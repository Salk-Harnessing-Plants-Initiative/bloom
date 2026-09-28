"""Unit tests for the RNA-seq dispatch worker loop: claim, submit, and complete or
fail, with a fake Supabase client and a fake submission (no database or cluster)."""

import pytest

import rnaseq_worker as worker
from k8s_client import K8sAlreadyExistsError, K8sConfigError, K8sSubmissionError
from rnaseq_workflows import WorkflowType

RUN = {
    "run_id": 7,
    "sample": "tinygex",
    "reference": "tiny_ref",
    "run_key": "tinygex__tiny_ref__u",
    "msg_id": 12,
}
BODY = {"metadata": {"name": "scrna-cellranger-dev-7-abcd1234"}}


class _Result:
    def __init__(self, data):
        self.data = data


class FakeClient:
    """Records RPC calls; claim returns the next queued result for its function."""

    def __init__(self, claims=None, fail_on=()):
        self.claims = {k: list(v) for k, v in (claims or {}).items()}
        self.fail_on = set(fail_on)
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        client = self

        class _Call:
            def execute(self):
                if name in client.fail_on:
                    raise RuntimeError(f"{name} unavailable")
                queue = client.claims.get(name)
                return _Result(queue.pop(0) if queue else None)

        return _Call()

    def names(self):
        return [name for name, _ in self.calls]


def _wf(name="scrna-cellranger", build=lambda run: dict(BODY)):
    return WorkflowType(
        name=name,
        claim_fn=f"claim_{name}",
        complete_fn=f"complete_{name}",
        fail_fn=f"fail_{name}",
        build_body=build,
    )


WF = _wf()


@pytest.fixture(autouse=True)
def _reset_running():
    worker._running = True
    yield
    worker._running = True


@pytest.fixture
def submitted(monkeypatch):
    bodies = []

    def _submit(body):
        bodies.append(body)
        return body["metadata"]["name"]

    monkeypatch.setattr(worker, "submit_workflow", _submit)
    return bodies


def _raise(exc):
    def _submit(body):
        raise exc

    return _submit


# --------------------------------------------------------------------------- #
# process_one
# --------------------------------------------------------------------------- #


def test_an_empty_queue_claims_nothing_and_submits_nothing(submitted):
    client = FakeClient()
    assert worker.process_one(client, WF) is False
    assert client.names() == ["claim_scrna-cellranger"]
    assert submitted == []


def test_the_claim_passes_the_visibility_timeout_and_redelivery_limit(submitted):
    client = FakeClient()
    worker.process_one(client, WF)
    assert client.calls[0][1] == {
        "p_vt": worker.VISIBILITY_TIMEOUT,
        "p_max_reads": worker.MAX_READS,
    }


def test_a_failed_claim_is_treated_as_nothing_claimed(submitted):
    client = FakeClient(fail_on={"claim_scrna-cellranger"})
    assert worker.process_one(client, WF) is False
    assert submitted == []


def test_a_claimed_run_is_submitted_and_recorded(submitted):
    client = FakeClient(claims={"claim_scrna-cellranger": [[RUN]]})
    assert worker.process_one(client, WF) is True
    assert submitted == [BODY]
    assert client.calls[1] == (
        "complete_scrna-cellranger",
        {
            "p_run_id": 7,
            "p_msg_id": 12,
            "p_argo_workflow_name": "scrna-cellranger-dev-7-abcd1234",
        },
    )


def test_the_body_is_built_from_the_claimed_run(submitted):
    seen = []
    wf = _wf(build=lambda run: seen.append(run) or dict(BODY))
    worker.process_one(FakeClient(claims={"claim_scrna-cellranger": [[RUN]]}), wf)
    assert seen == [RUN]


def test_an_already_existing_workflow_is_recorded_as_submitted(monkeypatch):
    monkeypatch.setattr(
        worker, "submit_workflow", _raise(K8sAlreadyExistsError("exists"))
    )
    client = FakeClient(claims={"claim_scrna-cellranger": [[RUN]]})
    assert worker.process_one(client, WF) is True
    assert client.names() == ["claim_scrna-cellranger", "complete_scrna-cellranger"]
    assert client.calls[1][1]["p_argo_workflow_name"] == BODY["metadata"]["name"]


def test_a_rejected_submission_fails_the_run_with_a_generic_message(monkeypatch):
    monkeypatch.setattr(
        worker, "submit_workflow", _raise(K8sSubmissionError("detail with a URL"))
    )
    client = FakeClient(claims={"claim_scrna-cellranger": [[RUN]]})
    assert worker.process_one(client, WF) is True
    assert client.calls[1] == (
        "fail_scrna-cellranger",
        {"p_run_id": 7, "p_msg_id": 12, "p_message": worker.SUBMISSION_FAILED},
    )
    assert "complete_scrna-cellranger" not in client.names()


def test_a_failing_fail_call_is_logged_not_raised(monkeypatch):
    monkeypatch.setattr(worker, "submit_workflow", _raise(K8sSubmissionError("x")))
    client = FakeClient(
        claims={"claim_scrna-cellranger": [[RUN]]}, fail_on={"fail_scrna-cellranger"}
    )
    assert worker.process_one(client, WF) is True


@pytest.mark.parametrize("where", ["build", "submit"])
def test_a_config_error_leaves_the_run_for_redelivery(monkeypatch, where):
    wf = WF
    if where == "build":
        wf = _wf(build=_raise(K8sConfigError("no env label")))
    else:
        monkeypatch.setattr(
            worker, "submit_workflow", _raise(K8sConfigError("no token"))
        )
    client = FakeClient(claims={"claim_scrna-cellranger": [[RUN]]})
    assert worker.process_one(client, wf) is True
    assert client.names() == ["claim_scrna-cellranger"]


def test_a_failed_completion_never_fails_a_submitted_run(submitted):
    client = FakeClient(
        claims={"claim_scrna-cellranger": [[RUN]]},
        fail_on={"complete_scrna-cellranger"},
    )
    assert worker.process_one(client, WF) is True
    assert "fail_scrna-cellranger" not in client.names()


# --------------------------------------------------------------------------- #
# process_all and the loop
# --------------------------------------------------------------------------- #


def test_every_registered_type_is_tried_in_one_pass(monkeypatch, submitted):
    a, b = _wf("type-a"), _wf("type-b")
    monkeypatch.setattr(worker, "WORKFLOW_TYPES", (a, b))
    client = FakeClient(claims={"claim_type-b": [[RUN]]})
    assert worker.process_all(client) is True
    assert client.names() == ["claim_type-a", "claim_type-b", "complete_type-b"]


def test_a_pass_with_nothing_queued_reports_idle(monkeypatch, submitted):
    monkeypatch.setattr(worker, "WORKFLOW_TYPES", (_wf("type-a"), _wf("type-b")))
    assert worker.process_all(FakeClient()) is False


def test_a_stop_signal_ends_the_pass_before_the_next_type(monkeypatch, submitted):
    a, b = _wf("type-a"), _wf("type-b")
    monkeypatch.setattr(worker, "WORKFLOW_TYPES", (a, b))

    def _submit_then_stop(body):
        worker._running = False
        return body["metadata"]["name"]

    monkeypatch.setattr(worker, "submit_workflow", _submit_then_stop)
    client = FakeClient(claims={"claim_type-a": [[RUN]], "claim_type-b": [[RUN]]})
    worker.process_all(client)
    assert client.names() == ["claim_type-a", "complete_type-a"]


def test_the_loop_sleeps_only_when_nothing_was_claimed(monkeypatch):
    results = iter([True, False])
    sleeps = []

    def _pass(client):
        handled = next(results)
        if not handled:
            worker._running = False
        return handled

    monkeypatch.setattr(worker, "app_client", lambda: object())
    monkeypatch.setattr(worker, "process_all", _pass)
    monkeypatch.setattr(worker.time, "sleep", sleeps.append)
    monkeypatch.setattr(worker.signal, "signal", lambda *a: None)
    worker.run()
    assert sleeps == [worker.POLL_INTERVAL]


def test_the_loop_reconnects_after_an_unexpected_error(monkeypatch):
    clients = []
    attempts = iter([RuntimeError("session expired"), False])

    def _pass(client):
        outcome = next(attempts)
        if isinstance(outcome, Exception):
            raise outcome
        worker._running = False
        return outcome

    def _connect():
        clients.append(object())
        return clients[-1]

    monkeypatch.setattr(worker, "app_client", _connect)
    monkeypatch.setattr(worker, "process_all", _pass)
    monkeypatch.setattr(worker.time, "sleep", lambda s: None)
    monkeypatch.setattr(worker.signal, "signal", lambda *a: None)
    worker.run()
    assert len(clients) == 2


def test_startup_retries_until_supabase_is_reachable(monkeypatch):
    outcomes = iter([RuntimeError("down"), RuntimeError("down"), object()])

    def _connect():
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(worker, "app_client", _connect)
    monkeypatch.setattr(worker.time, "sleep", lambda s: None)
    assert worker._connect_with_retry() is not None


def test_a_stop_signal_while_waiting_to_connect_exits_cleanly(monkeypatch):
    def _connect():
        worker._running = False
        raise RuntimeError("down")

    monkeypatch.setattr(worker, "app_client", _connect)
    monkeypatch.setattr(worker.time, "sleep", lambda s: None)
    assert worker._connect_with_retry() is None
