"""Unit tests for the RNA-seq status poller: which runs it reads, what it records for
each Workflow state, a Workflow that is gone, and how errors and signals are handled.
Uses a fake Supabase client and a fake get_workflow (no database or cluster)."""

import json
from pathlib import Path

import pytest

import rnaseq_status_poller as poller
from k8s_client import K8sConfigError, K8sStatusError
from rnaseq_status import RunStatus
from rnaseq_workflows import WorkflowType

FIXTURES = Path(__file__).parent / "fixtures" / "argo"
RUN = {
    "id": 7,
    "workflow_type": "scrna-cellranger",
    "params": {"sample": "tinygex", "reference": "tiny_ref"},
    "run_key": "tinygex__tiny_ref__poller-sample-ok",
    "status": "submitted",
    "argo_workflow_name": "scrna-cellranger-dev-900001-a5d34435",
}


class _Result:
    def __init__(self, data):
        self.data = data


class FakeClient:
    """Serves `rows` for the active-runs query and records every RPC."""

    def __init__(self, rows=(), changed=True, fail_rpc_for=()):
        self.rows = list(rows)
        self.changed = changed
        self.fail_rpc_for = set(fail_rpc_for)
        self.rpcs = []
        self.queries = []

    def table(self, name):
        client = self

        class _Query:
            def __init__(self):
                self.calls = [("table", name)]

            def select(self, cols):
                self.calls.append(("select", cols))
                return self

            def in_(self, col, values):
                self.calls.append(("in", col, tuple(values)))
                return self

            def order(self, col):
                self.calls.append(("order", col))
                return self

            def execute(self):
                client.queries.append(self.calls)
                return _Result(list(client.rows))

        return _Query()

    def rpc(self, name, params):
        self.rpcs.append((name, params))
        client = self

        class _Call:
            def execute(self):
                if params.get("p_run_id") in client.fail_rpc_for:
                    raise RuntimeError("rpc unavailable")
                return _Result(client.changed)

        return _Call()


@pytest.fixture(autouse=True)
def _reset_running():
    poller._running = True
    yield
    poller._running = True


@pytest.fixture
def workflows(monkeypatch):
    """Maps a Workflow name to what get_workflow returns (a dict, None, or an exception)."""
    served = {}

    def _get(name):
        value = served.get(name)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(poller, "get_workflow", _get)
    return served


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"cellranger_{name}.json").read_text())


def _reader(status):
    return WorkflowType("scrna-cellranger", lambda run: {}, lambda wf, run: status)


# --------------------------------------------------------------------------- #
# Which runs
# --------------------------------------------------------------------------- #


def test_only_submitted_and_running_runs_are_read(workflows):
    client = FakeClient()
    poller.sweep_once(client)
    (query,) = client.queries
    assert ("table", "rnaseq_runs") in query
    assert ("in", "status", ("submitted", "running")) in query


def test_an_empty_sweep_records_nothing(workflows):
    client = FakeClient()
    assert poller.sweep_once(client) == (0, 0)
    assert client.rpcs == []


# --------------------------------------------------------------------------- #
# What gets recorded
# --------------------------------------------------------------------------- #


def test_a_real_counting_workflow_is_recorded_as_running_at_count(workflows):
    workflows[RUN["argo_workflow_name"]] = _fixture("counting")
    client = FakeClient([RUN])
    poller.sweep_once(client)
    ((name, params),) = client.rpcs
    assert name == "update_rnaseq_run_status"
    assert params["p_run_id"] == 7
    assert (params["p_status"], params["p_current_step"]) == ("running", "count")
    assert set(params["p_step_pods"]) == {"stage-reference", "stage", "qc", "count"}
    assert (params["p_exit_code"], params["p_message"]) == (None, None)


def test_a_real_finished_workflow_is_recorded_with_its_message(workflows):
    workflows[RUN["argo_workflow_name"]] = _fixture("succeeded")
    client = FakeClient([RUN])
    poller.sweep_once(client)
    params = client.rpcs[0][1]
    assert (params["p_status"], params["p_exit_code"]) == ("succeeded", 0)
    assert params["p_message"].startswith("Finished: results in s3://")


def test_a_workflow_that_is_gone_fails_the_run(workflows):
    workflows[RUN["argo_workflow_name"]] = None
    client = FakeClient([RUN])
    poller.sweep_once(client)
    params = client.rpcs[0][1]
    assert (params["p_status"], params["p_message"]) == (
        "failed",
        poller.REMOVED_MESSAGE,
    )
    assert params["p_current_step"] is None and params["p_step_pods"] is None


def test_nothing_is_recorded_before_a_step_starts(workflows, monkeypatch):
    monkeypatch.setattr(poller, "WORKFLOW_TYPES", {"scrna-cellranger": _reader(None)})
    workflows[RUN["argo_workflow_name"]] = {"metadata": {"name": "x"}}
    client = FakeClient([RUN])
    poller.sweep_once(client)
    assert client.rpcs == []


def test_empty_step_pods_keep_the_stored_ones(workflows, monkeypatch):
    status = RunStatus(
        "failed", None, {}, None, "The workflow failed before a step ran"
    )
    monkeypatch.setattr(poller, "WORKFLOW_TYPES", {"scrna-cellranger": _reader(status)})
    workflows[RUN["argo_workflow_name"]] = {"metadata": {"name": "x"}}
    client = FakeClient([RUN])
    poller.sweep_once(client)
    assert client.rpcs[0][1]["p_step_pods"] is None


def test_an_unchanged_report_is_not_an_error(workflows):
    workflows[RUN["argo_workflow_name"]] = _fixture("counting")
    client = FakeClient([RUN], changed=False)
    assert poller.sweep_once(client) == (1, 0)


@pytest.mark.parametrize(
    "row",
    [{**RUN, "workflow_type": "fastqc"}, {**RUN, "argo_workflow_name": None}],
)
def test_a_run_the_poller_cannot_follow_is_left_alone(workflows, row):
    client = FakeClient([row])
    assert poller.sweep_once(client) == (1, 0)
    assert client.rpcs == []


# --------------------------------------------------------------------------- #
# Errors and signals
# --------------------------------------------------------------------------- #


def test_one_runs_error_does_not_stop_the_others(workflows):
    bad = {**RUN, "id": 8, "argo_workflow_name": "wf-bad"}
    good = {**RUN, "id": 9}
    workflows["wf-bad"] = K8sStatusError("Argo Workflow status check failed")
    workflows[RUN["argo_workflow_name"]] = _fixture("counting")
    client = FakeClient([bad, good])
    assert poller.sweep_once(client) == (2, 1)
    assert [p["p_run_id"] for _, p in client.rpcs] == [9]


def test_a_failed_database_call_counts_as_that_runs_error(workflows):
    workflows[RUN["argo_workflow_name"]] = _fixture("counting")
    other = {**RUN, "id": 10}
    client = FakeClient([RUN, other], fail_rpc_for={7})
    assert poller.sweep_once(client) == (2, 1)
    assert [p["p_run_id"] for _, p in client.rpcs] == [7, 10]


def test_missing_k8s_settings_stop_the_sweep(workflows):
    workflows[RUN["argo_workflow_name"]] = K8sConfigError("no token")
    client = FakeClient([RUN, {**RUN, "id": 11}])
    assert poller.sweep_once(client) == (2, 2)
    assert client.rpcs == []


def test_a_stop_signal_ends_the_sweep_before_the_next_run(workflows, monkeypatch):
    workflows[RUN["argo_workflow_name"]] = _fixture("counting")

    def _record_then_stop(client, run_id, status):
        poller._running = False
        return True

    monkeypatch.setattr(poller, "_record", _record_then_stop)
    client = FakeClient([RUN, {**RUN, "id": 12}])
    poller.sweep_once(client)
    assert poller._running is False


def test_the_loop_sleeps_between_sweeps(monkeypatch):
    sweeps, sleeps = [], []

    def _sweep(client):
        sweeps.append(client)
        if len(sweeps) == 2:
            poller._running = False
        return (0, 0)

    monkeypatch.setattr(poller, "app_client", lambda: object())
    monkeypatch.setattr(poller, "sweep_once", _sweep)
    monkeypatch.setattr(poller.time, "sleep", sleeps.append)
    monkeypatch.setattr(poller.signal, "signal", lambda *a: None)
    poller.run()
    assert len(sweeps) == 2
    assert sleeps == [poller.POLL_INTERVAL]


def test_the_loop_reconnects_after_a_failed_sweep(monkeypatch):
    clients = []
    outcomes = iter([RuntimeError("session expired"), (0, 0)])

    def _sweep(client):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        poller._running = False
        return outcome

    def _connect():
        clients.append(object())
        return clients[-1]

    monkeypatch.setattr(poller, "app_client", _connect)
    monkeypatch.setattr(poller, "sweep_once", _sweep)
    monkeypatch.setattr(poller.time, "sleep", lambda s: None)
    monkeypatch.setattr(poller.signal, "signal", lambda *a: None)
    poller.run()
    assert len(clients) == 2


def test_a_stop_signal_sets_the_flag():
    poller._stop(15, None)
    assert poller._running is False


def test_startup_retries_until_supabase_is_reachable(monkeypatch):
    outcomes = iter([RuntimeError("down"), object()])

    def _connect():
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(poller, "app_client", _connect)
    monkeypatch.setattr(poller.time, "sleep", lambda s: None)
    assert poller._connect_with_retry() is not None


@pytest.mark.parametrize(
    "raw, expected",
    [(None, 15.0), ("30", 30.0), ("abc", 15.0), ("0", 15.0), ("-1", 15.0)],
)
def test_the_poll_interval_falls_back_to_15_seconds(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("WORKFLOWS_STATUS_POLL_SECONDS", raising=False)
    else:
        monkeypatch.setenv("WORKFLOWS_STATUS_POLL_SECONDS", raw)
    assert poller._resolve_poll_interval() == expected
