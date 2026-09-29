"""Unit tests for the RNA-seq dispatch worker loop: claim, route by workflow type,
submit, and complete or fail, with a fake Supabase client and a fake submission (no
database or cluster)."""

import pytest

import rnaseq_worker as worker
from k8s_client import K8sAlreadyExistsError, K8sConfigError, K8sSubmissionError
from rnaseq_workflows import WorkflowType

RUN = {
    "run_id": 7,
    "workflow_type": "scrna-cellranger",
    "params": {"sample": "tinygex", "reference": "tiny_ref"},
    "run_key": "tinygex__tiny_ref__u",
    "msg_id": 12,
}
BODY = {"metadata": {"name": "scrna-cellranger-dev-7-abcd1234"}}
CLAIM, COMPLETE, FAIL = "claim_rnaseq_run", "complete_rnaseq_run", "fail_rnaseq_run"


class _Result:
    def __init__(self, data):
        self.data = data


class FakeClient:
    """Records RPC calls. Claim returns the next queued result; complete and fail
    return `changed`."""

    def __init__(self, claims=(), fail_on=(), changed=True):
        self.claims = list(claims)
        self.fail_on = set(fail_on)
        self.changed = changed
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        client = self

        class _Call:
            def execute(self):
                if name in client.fail_on:
                    raise RuntimeError(f"{name} unavailable")
                if name == CLAIM:
                    return _Result(client.claims.pop(0) if client.claims else [])
                return _Result(client.changed)

        return _Call()

    def names(self):
        return [name for name, _ in self.calls]


@pytest.fixture(autouse=True)
def _reset_running():
    worker._running = True
    yield
    worker._running = True


@pytest.fixture
def types(monkeypatch):
    """One registered type whose body builder records what it was given."""
    built = []

    def _build(run):
        built.append(run)
        return dict(BODY)

    registry = {"scrna-cellranger": WorkflowType("scrna-cellranger", _build)}
    monkeypatch.setattr(worker, "WORKFLOW_TYPES", registry)
    return built


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


def test_an_empty_queue_claims_nothing_and_submits_nothing(types, submitted):
    client = FakeClient()
    assert worker.process_one(client) is False
    assert client.names() == [CLAIM]
    assert submitted == []


def test_the_claim_passes_the_visibility_timeout_and_redelivery_limit(types):
    client = FakeClient()
    worker.process_one(client)
    assert client.calls[0] == (
        CLAIM,
        {"p_vt": worker.VISIBILITY_TIMEOUT, "p_max_reads": worker.MAX_READS},
    )


def test_a_failed_claim_is_treated_as_nothing_claimed(types, submitted):
    client = FakeClient(fail_on={CLAIM})
    assert worker.process_one(client) is False
    assert submitted == []


def test_a_claimed_run_is_submitted_and_recorded(types, submitted):
    client = FakeClient(claims=[[RUN]])
    assert worker.process_one(client) is True
    assert submitted == [BODY]
    assert client.calls[1] == (
        COMPLETE,
        {
            "p_run_id": 7,
            "p_msg_id": 12,
            "p_argo_workflow_name": "scrna-cellranger-dev-7-abcd1234",
        },
    )


def test_the_body_is_built_by_the_runs_workflow_type(types, submitted):
    worker.process_one(FakeClient(claims=[[RUN]]))
    assert types == [RUN]


def test_a_run_of_an_unhandled_type_is_failed_without_submitting(types, submitted):
    run = {**RUN, "workflow_type": "fastqc"}
    client = FakeClient(claims=[[run]])
    assert worker.process_one(client) is True
    assert submitted == []
    assert client.calls[1] == (
        FAIL,
        {
            "p_run_id": 7,
            "p_msg_id": 12,
            "p_message": "workflow type 'fastqc' is not supported",
        },
    )


def test_an_already_existing_workflow_is_recorded_as_submitted(types, monkeypatch):
    monkeypatch.setattr(
        worker, "submit_workflow", _raise(K8sAlreadyExistsError("exists"))
    )
    client = FakeClient(claims=[[RUN]])
    assert worker.process_one(client) is True
    assert client.names() == [CLAIM, COMPLETE]
    assert client.calls[1][1]["p_argo_workflow_name"] == BODY["metadata"]["name"]


def test_a_rejected_submission_fails_the_run_with_a_generic_message(types, monkeypatch):
    monkeypatch.setattr(
        worker, "submit_workflow", _raise(K8sSubmissionError("detail with a URL"))
    )
    client = FakeClient(claims=[[RUN]])
    assert worker.process_one(client) is True
    assert client.calls[1] == (
        FAIL,
        {"p_run_id": 7, "p_msg_id": 12, "p_message": worker.SUBMISSION_FAILED},
    )
    assert COMPLETE not in client.names()


@pytest.mark.parametrize("unhandled", [False, True])
def test_a_failing_fail_call_is_logged_not_raised(types, monkeypatch, unhandled):
    monkeypatch.setattr(worker, "submit_workflow", _raise(K8sSubmissionError("x")))
    run = {**RUN, "workflow_type": "fastqc"} if unhandled else RUN
    client = FakeClient(claims=[[run]], fail_on={FAIL})
    assert worker.process_one(client) is True


@pytest.mark.parametrize("where", ["build", "submit"])
def test_a_config_error_leaves_the_run_for_redelivery(monkeypatch, where):
    build = lambda run: dict(BODY)  # noqa: E731
    if where == "build":
        build = _raise(K8sConfigError("no env label"))
    else:
        monkeypatch.setattr(
            worker, "submit_workflow", _raise(K8sConfigError("no token"))
        )
    monkeypatch.setattr(
        worker,
        "WORKFLOW_TYPES",
        {"scrna-cellranger": WorkflowType("scrna-cellranger", build)},
    )
    client = FakeClient(claims=[[RUN]])
    assert worker.process_one(client) is True
    assert client.names() == [CLAIM]


def test_a_failed_completion_never_fails_a_submitted_run(types, submitted):
    client = FakeClient(claims=[[RUN]], fail_on={COMPLETE})
    assert worker.process_one(client) is True
    assert FAIL not in client.names()


def test_a_completion_that_changed_nothing_is_not_treated_as_an_error(types, submitted):
    client = FakeClient(claims=[[RUN]], changed=False)
    assert worker.process_one(client) is True
    assert client.names() == [CLAIM, COMPLETE]


def test_the_registered_types_are_keyed_by_their_workflow_type():
    import rnaseq_workflows

    assert all(key == wf.name for key, wf in rnaseq_workflows.WORKFLOW_TYPES.items())


# --------------------------------------------------------------------------- #
# The loop
# --------------------------------------------------------------------------- #


def test_the_loop_sleeps_only_when_nothing_was_claimed(monkeypatch):
    results = iter([True, False])
    sleeps = []

    def _one(client):
        handled = next(results)
        if not handled:
            worker._running = False
        return handled

    monkeypatch.setattr(worker, "app_client", lambda: object())
    monkeypatch.setattr(worker, "process_one", _one)
    monkeypatch.setattr(worker.time, "sleep", sleeps.append)
    monkeypatch.setattr(worker.signal, "signal", lambda *a: None)
    worker.run()
    assert sleeps == [worker.POLL_INTERVAL]


def test_the_loop_reconnects_after_an_unexpected_error(monkeypatch):
    clients = []
    attempts = iter([RuntimeError("session expired"), False])

    def _one(client):
        outcome = next(attempts)
        if isinstance(outcome, Exception):
            raise outcome
        worker._running = False
        return outcome

    def _connect():
        clients.append(object())
        return clients[-1]

    monkeypatch.setattr(worker, "app_client", _connect)
    monkeypatch.setattr(worker, "process_one", _one)
    monkeypatch.setattr(worker.time, "sleep", lambda s: None)
    monkeypatch.setattr(worker.signal, "signal", lambda *a: None)
    worker.run()
    assert len(clients) == 2


def test_a_stop_signal_ends_the_loop_after_the_current_run(monkeypatch):
    calls = []

    def _one(client):
        calls.append(client)
        worker._running = False
        return True

    monkeypatch.setattr(worker, "app_client", lambda: object())
    monkeypatch.setattr(worker, "process_one", _one)
    monkeypatch.setattr(worker.signal, "signal", lambda *a: None)
    worker.run()
    assert len(calls) == 1


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
