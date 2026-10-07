"""Unit tests for the RNA-seq dispatch worker loop: claim, route by workflow type,
submit, and complete or fail, with a fake Supabase client and a fake submission (no
database or cluster)."""

import logging

import pytest

import k8s_client
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
    return `changed`. Table reads return `tables[name]`, filtered by eq (none by default)."""

    def __init__(self, claims=(), fail_on=(), changed=True, tables=None):
        self.claims = list(claims)
        self.fail_on = set(fail_on)
        self.changed = changed
        self.tables = tables or {}
        self.calls = []

    def table(self, name):
        client = self

        class _Query:
            def __init__(self):
                self.filters = {}

            def select(self, _cols):
                return self

            def eq(self, col, value):
                self.filters[col] = value
                return self

            def execute(self):
                if name in client.fail_on:
                    raise RuntimeError(f"{name} unavailable")
                rows = client.tables.get(name, [])
                return _Result(
                    [
                        r
                        for r in rows
                        if all(r.get(k) == v for k, v in self.filters.items())
                    ]
                )

        return _Query()

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


def _no_status(workflow, run):
    return None


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

    registry = {
        "scrna-cellranger": WorkflowType("scrna-cellranger", _build, _no_status)
    }
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
    assert types == [{**RUN, "dataset": None}]


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
        {"scrna-cellranger": WorkflowType("scrna-cellranger", build, _no_status)},
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


def test_a_set_but_invalid_secret_name_warns_at_startup(monkeypatch, caplog):
    monkeypatch.setenv("WORKFLOWS_K8S_PIPELINE_SECRET_NAME", "Bloom-Secret ")
    monkeypatch.setattr(k8s_client, "PIPELINE_SECRET_NAME", None)
    with caplog.at_level(logging.WARNING, logger="rnaseq_worker"):
        worker._warn_if_pipeline_secret_invalid()
    assert "is not a valid Kubernetes object name" in caplog.text


@pytest.mark.parametrize("raw, name", [(None, None), ("bloom-secret", "bloom-secret")])
def test_an_unset_or_valid_secret_name_does_not_warn(monkeypatch, caplog, raw, name):
    if raw is None:
        monkeypatch.delenv("WORKFLOWS_K8S_PIPELINE_SECRET_NAME", raising=False)
    else:
        monkeypatch.setenv("WORKFLOWS_K8S_PIPELINE_SECRET_NAME", raw)
    monkeypatch.setattr(k8s_client, "PIPELINE_SECRET_NAME", name)
    with caplog.at_level(logging.WARNING, logger="rnaseq_worker"):
        worker._warn_if_pipeline_secret_invalid()
    assert caplog.text == ""


def test_the_secret_name_is_checked_when_the_worker_starts(monkeypatch):
    checked = []
    monkeypatch.setattr(
        worker, "_warn_if_pipeline_secret_invalid", lambda: checked.append(True)
    )
    monkeypatch.setattr(worker, "_connect_with_retry", lambda: None)
    monkeypatch.setattr(worker.signal, "signal", lambda *a: None)
    worker.run()
    assert checked == [True]


# --------------------------------------------------------------------------- #
# The dataset a run loads into Bloom
# --------------------------------------------------------------------------- #


def _with_metadata(metadata, species=({"id": 3, "common_name": "Arabidopsis"},)):
    return {
        "rnaseq_runs": [{"id": RUN["run_id"], "metadata": metadata}],
        "species": list(species),
    }


def test_a_run_naming_a_dataset_passes_its_name_and_species(types, submitted):
    tables = _with_metadata({"dataset_name": "  Root atlas ", "species_id": 3})
    worker.process_one(FakeClient(claims=[[RUN]], tables=tables))
    assert types[0]["dataset"] == {"name": "Root atlas", "species": "Arabidopsis"}


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        {},
        {"dataset_name": "", "species_id": 3},
        {"dataset_name": "Root atlas"},
        {"dataset_name": "Root atlas", "species_id": "3"},
        {"dataset_name": "Root atlas", "species_id": True},
    ],
    ids=[
        "no-metadata",
        "empty",
        "blank-name",
        "no-species",
        "species-as-text",
        "species-as-bool",
    ],
)
def test_a_run_without_a_usable_dataset_is_submitted_without_one(
    types, submitted, metadata
):
    worker.process_one(FakeClient(claims=[[RUN]], tables=_with_metadata(metadata)))
    assert types[0]["dataset"] is None
    assert submitted, "the run is still submitted, just not loaded"


def test_a_species_that_doesnt_exist_is_submitted_without_a_dataset(
    types, submitted, caplog
):
    tables = _with_metadata({"dataset_name": "Root atlas", "species_id": 99})
    with caplog.at_level(logging.WARNING, logger="rnaseq_worker"):
        worker.process_one(FakeClient(claims=[[RUN]], tables=tables))
    assert types[0]["dataset"] is None and "won't be loaded" in caplog.text


@pytest.mark.parametrize("table", ["rnaseq_runs", "species"])
def test_a_failed_lookup_leaves_the_run_queued(types, submitted, table):
    tables = _with_metadata({"dataset_name": "Root atlas", "species_id": 3})
    client = FakeClient(claims=[[RUN]], tables=tables, fail_on={table})
    assert worker.process_one(client) is True
    assert submitted == [] and types == []
    assert COMPLETE not in client.names() and FAIL not in client.names()
