"""Unit tests for the status poller (bloom #11 Phase 3): the rollup rule
(pure function), the per-run DB reads, and the sweep loop. Matches
test_dispatch_worker.py's convention (mock the DB-read + get_workflow_status
seam — no real K8s, DB, or httpx needed) plus a minimal fake fluent Supabase
client (matching test_pipeline.py's convention) for the two functions that
build real queries (`_fetch_candidate_runs`/`_fetch_effective_phases`)."""

import pytest

import status_poller as worker
from k8s_client import K8sConfigError, K8sStatusError

# --------------------------------------------------------------------------- #
# Fake fluent Supabase client — routes by table name, matching
# test_pipeline.py's convention.
# --------------------------------------------------------------------------- #


class _Result:
    def __init__(self, data):
        self.data = data


def _apply_filters(rows, filters):
    result = rows
    for kind, key, val in filters:
        if kind == "eq":
            result = [r for r in result if r.get(key) == val]
        elif kind == "in_":
            valset = set(val)
            result = [r for r in result if r.get(key) in valset]
    return result


class _Query:
    def __init__(self, client, table_name):
        self._client = client
        self._table = table_name
        self._filters = []

    def select(self, *a, **k):
        return self

    def eq(self, key, val):
        self._filters.append(("eq", key, val))
        return self

    def in_(self, key, vals):
        self._filters.append(("in_", key, list(vals)))
        return self

    def execute(self):
        rows = list(self._client._tables.get(self._table, []))
        rows = _apply_filters(rows, self._filters)
        return _Result(rows)


class _FakeClient:
    def __init__(self, **tables):
        self._tables = tables

    def table(self, name):
        return _Query(self, name)


@pytest.fixture(autouse=True)
def _reset_running():
    worker._running = True
    yield
    worker._running = True


# --- rollup: pure function ----------------------------------------------------


def test_rollup_running_when_any_phase_running():
    assert worker.rollup(["Succeeded", "Running"]) == "running"


def test_rollup_running_when_only_pending():
    assert worker.rollup(["Pending"]) == "running"


def test_rollup_complete_when_all_succeeded():
    assert worker.rollup(["Succeeded", "Succeeded"]) == "complete"


def test_rollup_failed_when_none_succeeded():
    assert worker.rollup(["Failed", "Error"]) == "failed"


def test_rollup_partial_when_mixed_terminal():
    assert worker.rollup(["Succeeded", "Failed"]) == "partial"


def test_rollup_returns_none_for_an_empty_phase_list_not_a_vacuous_complete():
    assert worker.rollup([]) is None


# --- _fetch_effective_phases: real query logic against a fake client --------


def test_rollup_treats_dispatch_failed_scan_as_effective_failed_phase(monkeypatch):
    client = _FakeClient(
        cyl_pipeline_run_scans=[
            {"run_id": 1, "argo_workflow_name": None, "status": "failed"},
            {"run_id": 1, "argo_workflow_name": "wf-a", "status": "queued"},
        ]
    )
    monkeypatch.setattr(worker, "get_workflow_status", lambda name: "Succeeded")
    phases = worker._fetch_effective_phases(client, run_id=1)
    assert sorted(phases) == ["Failed", "Succeeded"]
    assert worker.rollup(phases) == "partial"


def test_run_with_no_workflow_names_and_no_dispatch_failures_is_left_unchanged(
    monkeypatch,
):
    client = _FakeClient(
        cyl_pipeline_run_scans=[
            {"run_id": 1, "argo_workflow_name": None, "status": "queued"}
        ]
    )
    called = {"get_status": False}
    monkeypatch.setattr(
        worker, "get_workflow_status", lambda name: called.update(get_status=True)
    )
    phases = worker._fetch_effective_phases(client, run_id=1)
    assert phases == []
    assert called["get_status"] is False


def test_rollup_skips_a_404d_workflow_rather_than_guessing(monkeypatch):
    client = _FakeClient(
        cyl_pipeline_run_scans=[
            {"run_id": 1, "argo_workflow_name": "wf-gone", "status": "queued"}
        ]
    )
    checked = []
    monkeypatch.setattr(
        worker, "get_workflow_status", lambda name: checked.append(name) or None
    )
    phases = worker._fetch_effective_phases(client, run_id=1)
    assert checked == ["wf-gone"], "the workflow name must actually be looked up"
    assert phases == []
    assert worker.rollup(phases) is None


# --- _fetch_candidate_runs: real query logic against a fake client ---------


def test_sweep_selects_only_submitted_and_running_runs():
    client = _FakeClient(
        cyl_pipeline_runs=[
            {"id": 1, "status": "queued"},
            {"id": 2, "status": "submitted"},
            {"id": 3, "status": "running"},
            {"id": 4, "status": "complete"},
        ]
    )
    runs = worker._fetch_candidate_runs(client)
    assert {r["id"] for r in runs} == {2, 3}


# --- sweep_once: the full per-cycle loop ------------------------------------


def test_sweep_calls_update_with_the_computed_status(monkeypatch):
    calls = []
    monkeypatch.setattr(worker, "_fetch_candidate_runs", lambda c: [{"id": 1}])
    monkeypatch.setattr(worker, "_fetch_effective_phases", lambda c, r: ["Succeeded"])
    monkeypatch.setattr(
        worker, "update_run_status", lambda c, r, s: calls.append((r, s))
    )
    worker.sweep_once(object())
    assert calls == [(1, "complete")]


def test_sweep_with_no_candidate_runs_does_not_error(monkeypatch):
    monkeypatch.setattr(worker, "_fetch_candidate_runs", lambda c: [])

    def boom(*a, **k):
        raise AssertionError("must not be called for an empty candidate set")

    monkeypatch.setattr(worker, "_fetch_effective_phases", boom)
    monkeypatch.setattr(worker, "update_run_status", boom)
    worker.sweep_once(object())  # must not raise


def test_sweep_isolates_a_k8sstatuserror_on_one_run_from_the_rest(monkeypatch):
    calls = []
    monkeypatch.setattr(
        worker, "_fetch_candidate_runs", lambda c: [{"id": 1}, {"id": 2}]
    )

    def fake_fetch(client, run_id):
        if run_id == 1:
            raise K8sStatusError("Argo Workflow status check failed")
        return ["Succeeded"]

    monkeypatch.setattr(worker, "_fetch_effective_phases", fake_fetch)
    monkeypatch.setattr(
        worker, "update_run_status", lambda c, r, s: calls.append((r, s))
    )
    worker.sweep_once(object())
    assert calls == [(2, "complete")]


def test_sweep_leaves_a_run_unsettled_on_k8sconfigerror_and_continues_to_the_next_run(
    monkeypatch,
):
    fetch_attempts = []
    monkeypatch.setattr(
        worker, "_fetch_candidate_runs", lambda c: [{"id": 1}, {"id": 2}]
    )

    def fake_fetch(client, run_id):
        fetch_attempts.append(run_id)
        raise K8sConfigError("missing WORKFLOWS_K8S_TOKEN")

    monkeypatch.setattr(worker, "_fetch_effective_phases", fake_fetch)

    def update_boom(*a, **k):
        raise AssertionError("must not write anything on K8sConfigError")

    monkeypatch.setattr(worker, "update_run_status", update_boom)
    worker.sweep_once(object())  # must not raise
    assert fetch_attempts == [1, 2], "both runs must still be attempted this cycle"


def test_sweep_logs_and_continues_when_update_call_fails(monkeypatch):
    calls = []
    monkeypatch.setattr(
        worker, "_fetch_candidate_runs", lambda c: [{"id": 1}, {"id": 2}]
    )
    monkeypatch.setattr(worker, "_fetch_effective_phases", lambda c, r: ["Succeeded"])

    def fake_update(client, run_id, status):
        if run_id == 1:
            raise RuntimeError("connection reset")
        calls.append((run_id, status))

    monkeypatch.setattr(worker, "update_run_status", fake_update)
    worker.sweep_once(object())  # must not raise
    assert calls == [(2, "complete")], "the second run must still be updated"


# --- run(): connection retry, signal handling -------------------------------


def test_signal_during_sweep_lets_it_finish_before_exiting(monkeypatch):
    calls = []
    sweep_calls = {"n": 0}
    monkeypatch.setattr(worker, "app_client", lambda: object())

    def fake_fetch_candidates(client):
        sweep_calls["n"] += 1
        return [{"id": 1}]

    monkeypatch.setattr(worker, "_fetch_candidate_runs", fake_fetch_candidates)

    def fake_fetch_phases(client, run_id):
        # Simulate the OS delivering SIGTERM while this run's check is
        # in-flight — the signal handler only flips the running-flag; it
        # does not interrupt the current sweep, so this run's update still
        # completes.
        worker._stop(15, None)
        return ["Succeeded"]

    monkeypatch.setattr(worker, "_fetch_effective_phases", fake_fetch_phases)
    monkeypatch.setattr(
        worker, "update_run_status", lambda c, r, s: calls.append((r, s))
    )
    monkeypatch.setattr(worker.time, "sleep", lambda s: None)

    worker.run()

    assert calls == [(1, "complete")]
    assert sweep_calls["n"] == 1, "no new sweep cycle starts after the signal"


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

    sweep_calls = {"n": 0}

    def fake_fetch_candidates(client):
        sweep_calls["n"] += 1
        worker._running = False  # stop after reaching the main loop once
        return []

    monkeypatch.setattr(worker, "_fetch_candidate_runs", fake_fetch_candidates)

    worker.run()  # must not raise

    assert attempts["n"] == 3  # two failures, then success
    assert sweep_calls["n"] == 1  # reached the main loop once connected


def test_signal_while_waiting_to_connect_exits_cleanly(monkeypatch):
    attempts = {"n": 0}

    def fake_app_client():
        attempts["n"] += 1
        raise RuntimeError("still down")

    monkeypatch.setattr(worker, "app_client", fake_app_client)

    def fake_sleep(secs):
        worker._running = False  # signal arrives during the first retry backoff

    monkeypatch.setattr(worker.time, "sleep", fake_sleep)

    candidates_called = {"called": False}
    monkeypatch.setattr(
        worker,
        "_fetch_candidate_runs",
        lambda c: candidates_called.update(called=True),
    )

    worker.run()  # must return cleanly, not hang, not raise

    assert attempts["n"] == 1  # tried once, then stopped retrying after the signal
    assert candidates_called["called"] is False


# --- POLL_INTERVAL default ---------------------------------------------------


def test_poll_interval_defaults_to_15_when_env_var_unset(monkeypatch):
    monkeypatch.delenv("WORKFLOWS_STATUS_POLL_SECONDS", raising=False)
    assert worker._resolve_poll_interval() == 15
