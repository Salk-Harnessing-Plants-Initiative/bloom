"""Unit tests for the status poller (bloom #11 Phase 3): the rollup rule
(pure function), the per-run DB reads, and the sweep loop. Matches
test_dispatch_worker.py's convention (mock the DB-read + get_workflow_status
seam — no real K8s, DB, or httpx needed) plus a minimal fake fluent Supabase
client (matching test_pipeline.py's convention) for the two functions that
build real queries (`_fetch_candidate_runs`/`_fetch_effective_phases`)."""

import pytest
from postgrest import APIError

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
            # Distractor row for a DIFFERENT run — proves .eq("run_id", ...)
            # actually filters, rather than this test passing vacuously
            # because every seeded row happens to already belong to run 1.
            {"run_id": 2, "argo_workflow_name": "wf-other-run", "status": "queued"},
        ]
    )
    checked = []
    monkeypatch.setattr(
        worker,
        "get_workflow_status",
        lambda name: checked.append(name) or "Succeeded",
    )
    (
        phases,
        any_unknown,
        done_count,
        failed_count,
        queued_workflow_names,
    ) = worker._fetch_effective_phases(client, run_id=1)
    assert checked == ["wf-a"], "must not look up a workflow belonging to another run"
    assert sorted(phases) == ["Failed", "Succeeded"]
    assert any_unknown is False
    assert worker.rollup(phases) == "partial"
    assert done_count == 0
    assert failed_count == 1  # the dispatch-failed scan (argo_workflow_name None)
    assert queued_workflow_names == ["wf-a"]


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
    (
        phases,
        any_unknown,
        done_count,
        failed_count,
        queued_workflow_names,
    ) = worker._fetch_effective_phases(client, run_id=1)
    assert phases == []
    assert any_unknown is False
    assert called["get_status"] is False
    assert (done_count, failed_count) == (0, 0)
    assert queued_workflow_names == [], (
        "argo_workflow_name is None — nothing to reconcile"
    )


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
    (
        phases,
        any_unknown,
        done_count,
        failed_count,
        queued_workflow_names,
    ) = worker._fetch_effective_phases(client, run_id=1)
    assert checked == ["wf-gone"], "the workflow name must actually be looked up"
    assert phases == []
    assert any_unknown is True
    assert worker.rollup(phases) is None
    assert (done_count, failed_count) == (0, 0)
    assert queued_workflow_names == ["wf-gone"], (
        "still 'queued' regardless of the 404 — a 404'd workflow can no longer be "
        "silently running, so its queued rows are reconciliation candidates too"
    )


def test_a_404_alongside_an_observed_succeeded_sibling_is_flagged_as_unknown(
    monkeypatch,
):
    """Distinct from the all-404 case above: one workflow IS observed
    (Succeeded) this cycle, and a sibling batch's workflow 404s. any_unknown
    must still be True even though phases is non-empty — sweep_once relies on
    this flag to avoid concluding 'complete' from incomplete information."""
    client = _FakeClient(
        cyl_pipeline_run_scans=[
            {"run_id": 1, "argo_workflow_name": "wf-a", "status": "queued"},
            {"run_id": 1, "argo_workflow_name": "wf-b-gone", "status": "queued"},
        ]
    )
    monkeypatch.setattr(
        worker,
        "get_workflow_status",
        lambda name: None if name == "wf-b-gone" else "Succeeded",
    )
    (
        phases,
        any_unknown,
        done_count,
        failed_count,
        queued_workflow_names,
    ) = worker._fetch_effective_phases(client, run_id=1)
    assert phases == ["Succeeded"]
    assert any_unknown is True
    assert (done_count, failed_count) == (0, 0)
    assert queued_workflow_names == ["wf-a", "wf-b-gone"]


# --- _fetch_candidate_runs: real query logic against a fake client ---------


def test_sweep_selects_submitted_running_and_partial_runs():
    client = _FakeClient(
        cyl_pipeline_runs=[
            {"id": 1, "status": "queued"},
            {"id": 2, "status": "submitted"},
            {"id": 3, "status": "running"},
            {"id": 4, "status": "complete"},
            {"id": 5, "status": "partial"},
        ]
    )
    runs = worker._fetch_candidate_runs(client)
    assert {r["id"] for r in runs} == {2, 3, 5}, (
        "'partial' runs may still have genuinely-dispatched batches whose "
        "real Argo outcome hasn't been checked — they must not be excluded "
        "from polling merely because Phase 2 already marked them terminal"
    )


# --- sweep_once: the full per-cycle loop ------------------------------------


def test_sweep_calls_update_with_the_computed_status(monkeypatch):
    calls = []
    monkeypatch.setattr(worker, "_fetch_candidate_runs", lambda c: [{"id": 1}])
    monkeypatch.setattr(
        worker, "_fetch_effective_phases", lambda c, r: (["Succeeded"], False, 3, 1, [])
    )
    monkeypatch.setattr(
        worker,
        "update_run_status",
        lambda c, r, s, d=None, f=None: calls.append((r, s, d, f)),
    )
    worker.sweep_once(object())
    assert calls == [(1, "complete", 3, 1)]


def test_sweep_once_computes_counts_from_real_scan_rows_and_passes_them_through(
    monkeypatch,
):
    """End-to-end through sweep_once with the REAL (unmocked) _fetch_effective_phases
    against a fixture of cyl_pipeline_run_scans rows mixing 'written'/'failed'/
    'queued' — proves the counting logic and the update_run_status call are
    actually wired together, not just each individually correct in isolation.
    The fixture's one 'queued' row (wf-a) would also trigger the round-2
    backstop reconciliation (rollup here is 'partial', not 'running') — that
    is its own concern with its own dedicated tests below, so it's mocked
    to a no-op here to keep this test's original counting-only intent."""
    calls = []
    monkeypatch.setattr(worker, "_fetch_candidate_runs", lambda c: [{"id": 1}])
    client = _FakeClient(
        cyl_pipeline_run_scans=[
            {"run_id": 1, "argo_workflow_name": "wf-a", "status": "written"},
            {"run_id": 1, "argo_workflow_name": "wf-a", "status": "written"},
            {"run_id": 1, "argo_workflow_name": None, "status": "failed"},
            {"run_id": 1, "argo_workflow_name": "wf-a", "status": "queued"},
        ]
    )
    monkeypatch.setattr(worker, "get_workflow_status", lambda name: "Succeeded")
    monkeypatch.setattr(worker, "_reconcile_unresolved_scans", lambda c, name: 0)
    monkeypatch.setattr(
        worker,
        "update_run_status",
        lambda c, r, s, d=None, f=None: calls.append((r, s, d, f)),
    )
    worker.sweep_once(client)
    # effective phases: ["Failed" (dispatch-failed scan), "Succeeded" (wf-a)]
    # -> rollup "partial"; done_count = 2 ('written'), failed_count = 1 ('failed')
    assert calls == [(1, "partial", 2, 1)]


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
        return (["Succeeded"], False, 1, 0, [])

    monkeypatch.setattr(worker, "_fetch_effective_phases", fake_fetch)
    monkeypatch.setattr(
        worker,
        "update_run_status",
        lambda c, r, s, d=None, f=None: calls.append((r, s)),
    )
    worker.sweep_once(object())
    assert calls == [(2, "complete")]


def test_sweep_isolates_a_generic_exception_fetching_phases_from_the_rest(monkeypatch):
    """Found during /review-pr: the per-run try/except originally only caught
    (K8sConfigError, K8sStatusError) — a generic DB-level exception (a
    transient PostgREST error, a timeout, the deadlock class this repo's own
    CI hit) must be isolated the same way, not just K8s-specific errors."""
    calls = []
    monkeypatch.setattr(
        worker, "_fetch_candidate_runs", lambda c: [{"id": 1}, {"id": 2}]
    )

    def fake_fetch(client, run_id):
        if run_id == 1:
            raise RuntimeError("connection reset by peer")
        return (["Succeeded"], False, 1, 0, [])

    monkeypatch.setattr(worker, "_fetch_effective_phases", fake_fetch)
    monkeypatch.setattr(
        worker,
        "update_run_status",
        lambda c, r, s, d=None, f=None: calls.append((r, s)),
    )
    worker.sweep_once(object())  # must not raise
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


def test_sweep_continues_when_fetch_candidate_runs_raises(monkeypatch):
    """Found during /review-pr: _fetch_candidate_runs itself (the loop's own
    iterable) had no guard at all — a transient failure there must not crash
    the process; it should be equivalent to finding zero candidates this
    cycle, with the next scheduled cycle retrying."""

    def boom(client):
        raise RuntimeError("PostgREST unreachable")

    monkeypatch.setattr(worker, "_fetch_candidate_runs", boom)

    def assert_not_called(*a, **k):
        raise AssertionError("must not be reached if candidates can't be fetched")

    monkeypatch.setattr(worker, "_fetch_effective_phases", assert_not_called)
    monkeypatch.setattr(worker, "update_run_status", assert_not_called)
    worker.sweep_once(object())  # must not raise


def test_sweep_logs_and_continues_when_update_call_fails(monkeypatch):
    calls = []
    monkeypatch.setattr(
        worker, "_fetch_candidate_runs", lambda c: [{"id": 1}, {"id": 2}]
    )
    monkeypatch.setattr(
        worker, "_fetch_effective_phases", lambda c, r: (["Succeeded"], False, 1, 0, [])
    )

    def fake_update(client, run_id, status, done_count=None, failed_count=None):
        if run_id == 1:
            raise RuntimeError("connection reset")
        calls.append((run_id, status))

    monkeypatch.setattr(worker, "update_run_status", fake_update)
    worker.sweep_once(object())  # must not raise
    assert calls == [(2, "complete")], "the second run must still be updated"


def test_sweep_withholds_complete_when_a_workflow_is_unresolved_this_cycle(
    monkeypatch,
):
    """Found during /review-pr: a workflow that TTL-expires before ever being
    observed as terminal must not let the run be silently marked 'complete'
    from the remaining, incomplete evidence."""
    calls = []
    monkeypatch.setattr(worker, "_fetch_candidate_runs", lambda c: [{"id": 1}])
    monkeypatch.setattr(
        worker, "_fetch_effective_phases", lambda c, r: (["Succeeded"], True, 1, 0, [])
    )
    monkeypatch.setattr(
        worker,
        "update_run_status",
        lambda c, r, s, d=None, f=None: calls.append((r, s)),
    )
    worker.sweep_once(object())
    assert calls == [], (
        "must not conclude 'complete' while a sibling workflow's real "
        "outcome was never confirmed this cycle"
    )


def test_sweep_still_concludes_failed_or_partial_despite_an_unresolved_workflow(
    monkeypatch,
):
    """A confirmed non-Succeeded phase already rules out 'complete' regardless
    of what an unresolved sibling workflow turns out to have been — 'partial'/
    'failed' conclusions are safe to write even with incomplete information,
    unlike 'complete'."""
    calls = []
    monkeypatch.setattr(worker, "_fetch_candidate_runs", lambda c: [{"id": 1}])
    monkeypatch.setattr(
        worker,
        "_fetch_effective_phases",
        lambda c, r: (["Failed", "Succeeded"], True, 1, 1, []),
    )
    monkeypatch.setattr(
        worker,
        "update_run_status",
        lambda c, r, s, d=None, f=None: calls.append((r, s)),
    )
    worker.sweep_once(object())
    assert calls == [(1, "partial")]


# --- round 2: sweep_once's clean/unclean return value ------------------------


def test_sweep_once_returns_true_on_a_fully_clean_cycle(monkeypatch):
    monkeypatch.setattr(worker, "_fetch_candidate_runs", lambda c: [{"id": 1}])
    monkeypatch.setattr(
        worker, "_fetch_effective_phases", lambda c, r: (["Succeeded"], False, 1, 0, [])
    )
    monkeypatch.setattr(
        worker, "update_run_status", lambda c, r, s, d=None, f=None: None
    )
    assert worker.sweep_once(object()) is True


def test_sweep_once_returns_false_when_fetching_candidates_raises(monkeypatch):
    def boom(client):
        raise RuntimeError("PostgREST unreachable")

    monkeypatch.setattr(worker, "_fetch_candidate_runs", boom)
    assert worker.sweep_once(object()) is False


def test_sweep_once_returns_false_when_a_run_has_an_isolated_error(monkeypatch):
    """Found during /review-pr round 2: sweep_once's per-run isolation (round
    1's own fix) means it no longer raises for a caught K8s/DB error — but
    run()'s reconnect logic needs SOME signal that this cycle wasn't clean.
    A second, error-free run in the same cycle must not mask the first run's
    error in the returned value."""
    monkeypatch.setattr(
        worker, "_fetch_candidate_runs", lambda c: [{"id": 1}, {"id": 2}]
    )

    def fake_fetch(client, run_id):
        if run_id == 1:
            raise RuntimeError("transient DB blip")
        return (["Succeeded"], False, 1, 0, [])

    monkeypatch.setattr(worker, "_fetch_effective_phases", fake_fetch)
    monkeypatch.setattr(
        worker, "update_run_status", lambda c, r, s, d=None, f=None: None
    )
    assert worker.sweep_once(object()) is False


def test_sweep_once_returns_false_when_an_update_call_fails(monkeypatch):
    monkeypatch.setattr(worker, "_fetch_candidate_runs", lambda c: [{"id": 1}])
    monkeypatch.setattr(
        worker, "_fetch_effective_phases", lambda c, r: (["Succeeded"], False, 1, 0, [])
    )

    def fake_update(client, run_id, status, done_count=None, failed_count=None):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(worker, "update_run_status", fake_update)
    assert worker.sweep_once(object()) is False


# --- fix-cyl-pipeline-run-scan-status: the same-value skip is REMOVED -------
# (previously "round 2: skip a write that would just reconfirm an unchanged
# status" — inverted, not deleted, since the old behavior is now wrong: a
# still-'running' run's done_count/failed_count can advance every cycle even
# while its overall status doesn't, so skipping the write would freeze the
# "N/M scans done" progress display at whatever it read on the run's first
# 'running' cycle.)


def test_sweep_still_writes_a_reconfirmed_running_run_every_cycle(monkeypatch):
    """Was test_sweep_skips_the_write_when_a_running_run_is_reconfirmed_running
    before fix-cyl-pipeline-run-scan-status removed the same-value skip
    entirely — the call must now happen every cycle a candidate run reaches
    this point, status-unchanged or not."""
    calls = []
    monkeypatch.setattr(
        worker, "_fetch_candidate_runs", lambda c: [{"id": 1, "status": "running"}]
    )
    monkeypatch.setattr(
        worker, "_fetch_effective_phases", lambda c, r: (["Running"], False, 2, 0, [])
    )
    monkeypatch.setattr(
        worker,
        "update_run_status",
        lambda c, r, s, d=None, f=None: calls.append((r, s, d, f)),
    )
    worker.sweep_once(object())
    assert calls == [(1, "running", 2, 0)], (
        "the write must happen even though status ('running') is unchanged "
        "from the known status — counts may have advanced"
    )


def test_sweep_writes_unconditionally_across_repeated_identical_cycles(monkeypatch):
    """The removed skip was unconditional, not merely 'skip only when nothing
    plausibly changed' — two consecutive sweeps against an IDENTICAL fixture
    (same known status, same phases, same counts) must both write. There is
    no persisted prior-cycle state to compare against — sweep_once recomputes
    everything fresh every cycle — so this is the only way to actually
    distinguish 'unconditional' from 'happened to be correct once'."""
    calls = []
    monkeypatch.setattr(
        worker, "_fetch_candidate_runs", lambda c: [{"id": 1, "status": "running"}]
    )
    monkeypatch.setattr(
        worker, "_fetch_effective_phases", lambda c, r: (["Running"], False, 2, 0, [])
    )
    monkeypatch.setattr(
        worker,
        "update_run_status",
        lambda c, r, s, d=None, f=None: calls.append((r, s, d, f)),
    )
    worker.sweep_once(object())
    worker.sweep_once(object())
    assert calls == [(1, "running", 2, 0), (1, "running", 2, 0)]


def test_sweep_still_writes_a_dispatch_settled_partial_runs_first_real_confirmation(
    monkeypatch,
):
    """Found during /review-pr round 3: a regression in round 2's own
    same-value-skip fix. _fetch_candidate_runs's 'status' column can't tell
    apart Phase 2's dispatch-time 'partial' guess (never yet checked by this
    poller) from a prior real 'partial' confirmation by this poller itself —
    both look identical: known_status == 'partial'. Before this fix, the very
    FIRST real sweep of a dispatch-'partial' run (one dispatch-failed scan,
    one dispatched scan that later Succeeds) would compute 'partial' again
    and silently skip the write — freezing completed_at at the dispatch-time
    stamp forever, exactly the bug round 1's completed_at fix closed. Unlike
    'running', 'partial' must always write regardless of the known status,
    since it cannot be trusted to mean "already poller-confirmed"."""
    calls = []
    monkeypatch.setattr(
        worker, "_fetch_candidate_runs", lambda c: [{"id": 1, "status": "partial"}]
    )
    monkeypatch.setattr(
        worker,
        "_fetch_effective_phases",
        lambda c, r: (["Failed", "Succeeded"], False, 1, 1, []),
    )
    monkeypatch.setattr(
        worker,
        "update_run_status",
        lambda c, r, s, d=None, f=None: calls.append((r, s)),
    )
    worker.sweep_once(object())
    assert calls == [(1, "partial")], (
        "a dispatch-settled 'partial' run's first real confirmation must "
        "still write, even though the computed value matches the known "
        "dispatch-time string"
    )


def test_sweep_still_writes_when_computed_status_differs_from_known_status(
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(
        worker, "_fetch_candidate_runs", lambda c: [{"id": 1, "status": "submitted"}]
    )
    monkeypatch.setattr(
        worker, "_fetch_effective_phases", lambda c, r: (["Running"], False, 0, 0, [])
    )
    monkeypatch.setattr(
        worker,
        "update_run_status",
        lambda c, r, s, d=None, f=None: calls.append((r, s)),
    )
    worker.sweep_once(object())
    assert calls == [(1, "running")]


def test_sweep_partial_run_with_a_still_running_workflow_resolves_to_running(
    monkeypatch,
):
    """No existing test drove a 'partial'-candidate row all the way to
    'running' through sweep_once — only the pure rollup() function covered
    the rule ordering in isolation (found during /review-pr round 2)."""
    calls = []
    monkeypatch.setattr(
        worker, "_fetch_candidate_runs", lambda c: [{"id": 1, "status": "partial"}]
    )
    monkeypatch.setattr(
        worker, "_fetch_effective_phases", lambda c, r: (["Running"], False, 0, 0, [])
    )
    monkeypatch.setattr(
        worker,
        "update_run_status",
        lambda c, r, s, d=None, f=None: calls.append((r, s)),
    )
    worker.sweep_once(object())
    assert calls == [(1, "running")]


# --- fix-cyl-pipeline-run-scan-status round 2: terminal-rollup reconciliation
# --- backstop (design.md's Decision 6) --------------------------------------


def test_sweep_reconciles_a_queued_scan_before_writing_a_terminal_status(monkeypatch):
    """A run whose rollup concludes a non-'running' status may still have a scan
    row stuck 'queued' — write-back never ran for it at all (its workflow failed
    before reaching write-back, or the write-back container never started). This
    run will never be polled again once its terminal status is written, so this
    is the last chance to close that scan out."""
    reconcile_calls = []
    update_calls = []
    monkeypatch.setattr(worker, "_fetch_candidate_runs", lambda c: [{"id": 1}])
    monkeypatch.setattr(
        worker,
        "_fetch_effective_phases",
        lambda c, r: (["Failed"], False, 0, 1, ["wf-a"]),
    )
    monkeypatch.setattr(
        worker,
        "_reconcile_unresolved_scans",
        lambda c, name: reconcile_calls.append(name) or 1,
    )
    monkeypatch.setattr(worker, "_count_done_and_failed", lambda c, r: (0, 2))
    monkeypatch.setattr(
        worker,
        "update_run_status",
        lambda c, r, s, d=None, f=None: update_calls.append((r, s, d, f)),
    )
    worker.sweep_once(object())
    assert reconcile_calls == ["wf-a"]
    # Counts written come from the fresh post-reconciliation recount (mocked
    # here to the same (0, 2) the old increment-based approach would also
    # have produced in this non-racing case), not from incrementing the
    # pre-reconciliation snapshot — see the dedicated staleness test below.
    assert update_calls == [(1, "failed", 0, 2)]


def test_sweep_reconciles_multiple_distinct_queued_workflow_names_once_each(
    monkeypatch,
):
    reconcile_calls = []
    update_calls = []
    monkeypatch.setattr(worker, "_fetch_candidate_runs", lambda c: [{"id": 1}])
    monkeypatch.setattr(
        worker,
        "_fetch_effective_phases",
        lambda c, r: (["Failed", "Failed"], False, 0, 0, ["wf-a", "wf-b"]),
    )
    monkeypatch.setattr(
        worker,
        "_reconcile_unresolved_scans",
        lambda c, name: reconcile_calls.append(name) or 1,
    )
    monkeypatch.setattr(worker, "_count_done_and_failed", lambda c, r: (0, 2))
    monkeypatch.setattr(
        worker,
        "update_run_status",
        lambda c, r, s, d=None, f=None: update_calls.append((r, s, d, f)),
    )
    worker.sweep_once(object())
    assert reconcile_calls == ["wf-a", "wf-b"]
    assert update_calls == [(1, "failed", 0, 2)]


def test_sweep_recomputes_counts_fresh_after_reconciling_instead_of_incrementing_a_stale_snapshot(
    monkeypatch,
):
    """Round 2 /review-pr finding: _fetch_effective_phases's done_count/failed_count
    are a snapshot taken BEFORE the per-workflow K8s phase lookups and the
    reconciliation RPC even run. If a scan's write-back genuinely resolves
    ('queued' -> 'written') in the window between that snapshot and the
    reconciliation call, the reconciliation RPC correctly leaves it alone (its
    WHERE status='queued' guard no longer matches) — but naively incrementing
    the STALE snapshot's failed_count by the reconciled count would never give
    that scan's completion credit in done_count either, permanently
    undercounting a run that then goes terminal and is never revisited. The
    fix re-derives both counts from a fresh read right after reconciling."""
    monkeypatch.setattr(worker, "_fetch_candidate_runs", lambda c: [{"id": 1}])
    # Stale snapshot: 0 done, 0 failed, one leftover queued row under wf-a.
    monkeypatch.setattr(
        worker,
        "_fetch_effective_phases",
        lambda c, r: (["Failed"], False, 0, 0, ["wf-a"]),
    )
    # The reconciliation call itself finds nothing left to fail — the one
    # queued row it would have touched already resolved to 'written' for
    # real, between the snapshot above and this call.
    monkeypatch.setattr(worker, "_reconcile_unresolved_scans", lambda c, name: 0)
    # A fresh recount now sees that real completion.
    monkeypatch.setattr(worker, "_count_done_and_failed", lambda c, r: (1, 0))
    calls = []
    monkeypatch.setattr(
        worker,
        "update_run_status",
        lambda c, r, s, d=None, f=None: calls.append((r, s, d, f)),
    )
    worker.sweep_once(object())
    assert calls == [(1, "failed", 1, 0)], (
        "must write the freshly-recounted done_count (1), not the stale "
        "snapshot's done_count (0) incremented only by the reconciled count"
    )


def test_sweep_does_not_reconcile_queued_rows_while_still_running(monkeypatch):
    """A 'queued' row under a workflow still genuinely Pending/Running isn't
    stuck, it just isn't done yet — reconciling it would record a false
    failure."""
    monkeypatch.setattr(worker, "_fetch_candidate_runs", lambda c: [{"id": 1}])
    monkeypatch.setattr(
        worker,
        "_fetch_effective_phases",
        lambda c, r: (["Running"], False, 0, 0, ["wf-a"]),
    )

    def boom(client, name):
        raise AssertionError("must not reconcile a still-running run's queued rows")

    monkeypatch.setattr(worker, "_reconcile_unresolved_scans", boom)
    monkeypatch.setattr(
        worker, "update_run_status", lambda c, r, s, d=None, f=None: None
    )
    worker.sweep_once(object())  # must not raise


def test_sweep_leaves_the_run_unsettled_when_reconciliation_itself_fails(
    monkeypatch,
):
    """If the reconciliation call fails, the status write for this run must be
    skipped entirely this cycle — writing the terminal status anyway would drop
    this run from the candidate set forever with its queued row still
    unresolved, since nothing would ever poll it again to retry."""
    monkeypatch.setattr(
        worker, "_fetch_candidate_runs", lambda c: [{"id": 1}, {"id": 2}]
    )

    def fake_fetch(client, run_id):
        if run_id == 1:
            return (["Failed"], False, 0, 0, ["wf-a"])
        return (["Succeeded"], False, 1, 0, [])

    monkeypatch.setattr(worker, "_fetch_effective_phases", fake_fetch)

    def boom(client, name):
        raise RuntimeError("transient reconciliation failure")

    monkeypatch.setattr(worker, "_reconcile_unresolved_scans", boom)
    calls = []
    monkeypatch.setattr(
        worker,
        "update_run_status",
        lambda c, r, s, d=None, f=None: calls.append((r, s)),
    )
    assert worker.sweep_once(object()) is False
    assert calls == [(2, "complete")], (
        "run 1's status write must be skipped (left unsettled, retried next "
        "cycle); run 2 must still be checked and updated"
    )


def test_sweep_skips_reconciliation_when_no_queued_rows_remain(monkeypatch):
    monkeypatch.setattr(worker, "_fetch_candidate_runs", lambda c: [{"id": 1}])
    monkeypatch.setattr(
        worker,
        "_fetch_effective_phases",
        lambda c, r: (["Succeeded"], False, 1, 0, []),
    )

    def boom(client, name):
        raise AssertionError("must not be called with no leftover queued rows")

    monkeypatch.setattr(worker, "_reconcile_unresolved_scans", boom)
    calls = []
    monkeypatch.setattr(
        worker,
        "update_run_status",
        lambda c, r, s, d=None, f=None: calls.append((r, s)),
    )
    worker.sweep_once(object())
    assert calls == [(1, "complete")]


def test_sweep_reconciles_a_queued_scan_even_when_rollup_concludes_complete(
    monkeypatch,
):
    """Named explicitly in design.md's Decision 6: every distinct workflow can
    resolve Succeeded (rollup 'complete') while one particular scan under that
    workflow never got its own write-back call — a scan, not a workflow, can be
    the thing that never ran. The backstop must not be gated on a non-'complete'
    status; it's gated on 'not running' plus a leftover queued row, and 'complete'
    satisfies both."""
    reconcile_calls = []
    monkeypatch.setattr(worker, "_fetch_candidate_runs", lambda c: [{"id": 1}])
    monkeypatch.setattr(
        worker,
        "_fetch_effective_phases",
        lambda c, r: (["Succeeded"], False, 3, 0, ["wf-a"]),
    )
    monkeypatch.setattr(
        worker,
        "_reconcile_unresolved_scans",
        lambda c, name: reconcile_calls.append(name) or 1,
    )
    monkeypatch.setattr(worker, "_count_done_and_failed", lambda c, r: (3, 1))
    calls = []
    monkeypatch.setattr(
        worker,
        "update_run_status",
        lambda c, r, s, d=None, f=None: calls.append((r, s, d, f)),
    )
    worker.sweep_once(object())
    assert reconcile_calls == ["wf-a"]
    assert calls == [(1, "complete", 3, 1)]


def test_sweep_withheld_complete_on_404_never_reaches_reconciliation(monkeypatch):
    """The existing withheld-'complete'-on-404 rule already `continue`s before
    this backstop's code runs at all — pin that ordering explicitly, since a
    queued row here must not be reconciled from incomplete evidence."""
    monkeypatch.setattr(worker, "_fetch_candidate_runs", lambda c: [{"id": 1}])
    monkeypatch.setattr(
        worker,
        "_fetch_effective_phases",
        lambda c, r: (["Succeeded"], True, 1, 0, ["wf-a"]),
    )

    def boom(client, name):
        raise AssertionError("must not reconcile while complete is withheld")

    monkeypatch.setattr(worker, "_reconcile_unresolved_scans", boom)

    def update_boom(*a, **k):
        raise AssertionError("must not write anything while complete is withheld")

    monkeypatch.setattr(worker, "update_run_status", update_boom)
    worker.sweep_once(object())  # must not raise


# --- round 2: a real (unmocked) K8sStatusError from get_workflow_status -----


def test_sweep_isolates_a_real_k8sstatuserror_from_get_workflow_status(monkeypatch):
    """Found during /review-pr round 2: every existing K8sStatusError
    isolation test monkeypatched _fetch_effective_phases wholesale rather
    than letting the real function's own get_workflow_status call raise —
    this exercises the real call path."""
    monkeypatch.setattr(worker, "_fetch_candidate_runs", lambda c: [{"id": 1}])
    client = _FakeClient(
        cyl_pipeline_run_scans=[
            {"run_id": 1, "argo_workflow_name": "wf-a", "status": "queued"},
        ]
    )

    def fake_candidates(c):
        return [{"id": 1}]

    monkeypatch.setattr(worker, "_fetch_candidate_runs", fake_candidates)

    def fake_get_status(name):
        raise K8sStatusError("Argo Workflow status check failed")

    monkeypatch.setattr(worker, "get_workflow_status", fake_get_status)

    def assert_not_called(*a, **k):
        raise AssertionError("must not write anything when the status check failed")

    monkeypatch.setattr(worker, "update_run_status", assert_not_called)

    result = worker.sweep_once(client)  # must not raise
    assert result is False


# --- fix-cyl-pipeline-run-scan-status: PGRST202 during the deploy-ordering --
# --- window is expected/transient, not a generic isolated error ------------


def test_sweep_treats_signature_not_found_as_expected_and_transient(monkeypatch):
    """During the brief window between this app's deploy and its accompanying
    migration actually applying, update_run_status calls the not-yet-migrated
    RPC signature and PostgREST raises APIError(code='PGRST202'). This must
    not mark the cycle unclean — a burst of these during a normal deploy
    should not also trigger run()'s proactive reconnect."""
    monkeypatch.setattr(worker, "_fetch_candidate_runs", lambda c: [{"id": 1}])
    monkeypatch.setattr(
        worker, "_fetch_effective_phases", lambda c, r: (["Succeeded"], False, 1, 0, [])
    )

    def fake_update(client, run_id, status, done_count=None, failed_count=None):
        raise APIError({"code": "PGRST202", "message": "function not found"})

    monkeypatch.setattr(worker, "update_run_status", fake_update)
    assert worker.sweep_once(object()) is True


def test_sweep_still_marks_unclean_for_a_non_pgrst202_apierror(monkeypatch):
    """Contrast case: an APIError that is NOT the signature-not-found code
    (e.g. an expired JWT) is a real problem and must still mark the cycle
    unclean, exactly like any other isolated error."""
    monkeypatch.setattr(worker, "_fetch_candidate_runs", lambda c: [{"id": 1}])
    monkeypatch.setattr(
        worker, "_fetch_effective_phases", lambda c, r: (["Succeeded"], False, 1, 0, [])
    )

    def fake_update(client, run_id, status, done_count=None, failed_count=None):
        raise APIError({"code": "PGRST301", "message": "JWT expired"})

    monkeypatch.setattr(worker, "update_run_status", fake_update)
    assert worker.sweep_once(object()) is False


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
        return (["Succeeded"], False, 1, 0, [])

    monkeypatch.setattr(worker, "_fetch_effective_phases", fake_fetch_phases)
    monkeypatch.setattr(
        worker,
        "update_run_status",
        lambda c, r, s, d=None, f=None: calls.append((r, s)),
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


# --- round 2: run() reconnects after consecutive unclean sweep cycles -------


def test_run_reconnects_after_three_consecutive_unclean_cycles(monkeypatch):
    """Found during /review-pr round 2: round 1's own per-run error isolation
    fix means sweep_once almost never raises anymore, silently disabling
    run()'s only reconnect mechanism. run() must now detect a run of unclean
    cycles itself and proactively fetch a fresh client."""
    client_calls = {"n": 0}

    def fake_app_client():
        client_calls["n"] += 1
        return object()

    monkeypatch.setattr(worker, "app_client", fake_app_client)
    monkeypatch.setattr(worker.time, "sleep", lambda s: None)

    sweep_calls = {"n": 0}

    def fake_sweep_once(client):
        sweep_calls["n"] += 1
        if sweep_calls["n"] >= 3:
            worker._running = False
        return False  # every cycle is unclean

    monkeypatch.setattr(worker, "sweep_once", fake_sweep_once)

    worker.run()

    assert sweep_calls["n"] == 3
    # One call to connect on startup, plus one proactive reconnect once the
    # 3rd consecutive unclean cycle is reached.
    assert client_calls["n"] == 2


def test_run_does_not_reconnect_after_a_single_isolated_error(monkeypatch):
    reconnects = {"n": 0}

    def counting_app_client():
        reconnects["n"] += 1
        return object()

    monkeypatch.setattr(worker, "app_client", counting_app_client)
    monkeypatch.setattr(worker.time, "sleep", lambda s: None)

    sweep_calls = {"n": 0}

    def fake_sweep_once(client):
        sweep_calls["n"] += 1
        if sweep_calls["n"] >= 4:
            worker._running = False
            return True
        # cycle 1 unclean, cycle 2 clean, cycle 3 unclean, cycle 4 clean —
        # never 3 IN A ROW, so no reconnect should ever fire.
        return sweep_calls["n"] % 2 == 0

    monkeypatch.setattr(worker, "sweep_once", fake_sweep_once)

    worker.run()

    assert sweep_calls["n"] == 4
    assert reconnects["n"] == 1, "only the initial connect — no proactive reconnect"


def test_run_survives_a_failed_proactive_reconnect_and_keeps_retrying(monkeypatch):
    """Found during /review-pr round 3: no test previously exercised
    app_client() itself raising inside the 3-consecutive-unclean-cycles
    proactive-reconnect branch. Confirmed by hand that Python never
    partially rebinds `client` on a failed assignment (`client =
    app_client()`), so the poller must keep the old client and keep
    retrying rather than crashing or ending up with an unbound `client`."""
    connect_attempts = {"n": 0}

    def flaky_app_client():
        connect_attempts["n"] += 1
        if connect_attempts["n"] == 1:
            return object()  # the initial startup connect succeeds
        if connect_attempts["n"] == 2:
            raise RuntimeError("Supabase still unreachable")  # 1st reconnect fails
        return object()  # 2nd reconnect attempt succeeds

    monkeypatch.setattr(worker, "app_client", flaky_app_client)
    monkeypatch.setattr(worker.time, "sleep", lambda s: None)

    sweep_calls = {"n": 0}

    def fake_sweep_once(client):
        sweep_calls["n"] += 1
        if sweep_calls["n"] >= 4:
            worker._running = False
        return False  # every cycle is unclean

    monkeypatch.setattr(worker, "sweep_once", fake_sweep_once)

    worker.run()  # must not raise / must not crash on the failed reconnect

    assert sweep_calls["n"] == 4
    # startup connect + failed reconnect attempt (cycle 3) + successful
    # reconnect attempt (cycle 4, since the counter wasn't reset by the
    # failed attempt and immediately re-triggers the threshold again)
    assert connect_attempts["n"] == 3


def test_run_reconnects_when_sweep_once_itself_raises_unexpectedly(monkeypatch):
    """Found during /review-pr round 3: no test made sweep_once itself raise
    (as opposed to returning False) to exercise run()'s outer except —
    the residual path meant to cover a genuinely-unexpected bug inside
    sweep_once's own control flow (not one of its internally-isolated
    per-run/per-cycle errors)."""
    client_calls = {"n": 0}

    def fake_app_client():
        client_calls["n"] += 1
        return object()

    monkeypatch.setattr(worker, "app_client", fake_app_client)
    monkeypatch.setattr(worker.time, "sleep", lambda s: None)

    sweep_calls = {"n": 0}

    def fake_sweep_once(client):
        sweep_calls["n"] += 1
        worker._running = False
        raise KeyError("id")  # a genuinely-unexpected bug, not an isolated error

    monkeypatch.setattr(worker, "sweep_once", fake_sweep_once)

    worker.run()  # must not raise

    assert sweep_calls["n"] == 1
    # One call to connect on startup, plus one reconnect from the outer
    # except's own recovery path.
    assert client_calls["n"] == 2


def test_run_does_not_reset_the_error_streak_when_the_outer_exceptions_own_reconnect_fails(
    monkeypatch,
):
    """Found during /review-pr round 3: the outer except (sweep_once itself
    raising) used to unconditionally reset consecutive_error_cycles to 0
    even when its own nested reconnect attempt failed — inconsistent with
    the proactive-reconnect path, which only resets on a successful
    reconnect. Confirmed behaviorally benign in practice (the outer path
    retries app_client() every cycle regardless of the counter), but fixed
    for consistency. This test builds a counter of 2 via two isolated-error
    cycles, then has sweep_once raise with a FAILING reconnect (which must
    NOT reset the counter back to 0), then one more isolated-error cycle —
    which should reach the threshold (2 -> 3) and trigger a second, this
    time successful, proactive reconnect. Under the old unconditional-reset
    behavior, the streak would restart at 0 after the raise and this 4th
    cycle would only bring it to 1, never reconnecting."""
    connect_attempts = {"n": 0}

    def flaky_app_client():
        connect_attempts["n"] += 1
        if connect_attempts["n"] == 1:
            return object()  # startup connect succeeds
        if connect_attempts["n"] == 2:
            raise RuntimeError("still down")  # cycle 3's outer-except reconnect fails
        return object()  # cycle 4's proactive reconnect succeeds

    monkeypatch.setattr(worker, "app_client", flaky_app_client)
    monkeypatch.setattr(worker.time, "sleep", lambda s: None)

    sweep_calls = {"n": 0}

    def fake_sweep_once(client):
        sweep_calls["n"] += 1
        if sweep_calls["n"] in (1, 2):
            return False  # two isolated-error cycles build the streak to 2
        if sweep_calls["n"] == 3:
            raise KeyError("id")  # outer except fires; its own reconnect fails
        worker._running = False
        return False  # cycle 4: unclean again — streak should now reach 3

    monkeypatch.setattr(worker, "sweep_once", fake_sweep_once)

    worker.run()

    assert sweep_calls["n"] == 4
    # startup + cycle 3's failed outer-except reconnect + cycle 4's
    # successful proactive reconnect — the 3rd connect attempt is reachable
    # only if the streak survived the failed reconnect at cycle 3 instead
    # of being reset to 0.
    assert connect_attempts["n"] == 3


# --- POLL_INTERVAL default ---------------------------------------------------


def test_poll_interval_defaults_to_15_when_env_var_unset(monkeypatch):
    monkeypatch.delenv("WORKFLOWS_STATUS_POLL_SECONDS", raising=False)
    assert worker._resolve_poll_interval() == 15


def test_poll_interval_falls_back_to_default_for_a_negative_value(monkeypatch):
    """Found during /review-pr round 5: a negative value parses fine as a
    float (no ValueError), but time.sleep() raises ValueError for a negative
    argument — an uncaught crash at every one of run()'s three sleep call
    sites, directly contradicting this function's own "never raises"
    docstring, which previously only guarded against non-numeric strings."""
    monkeypatch.setenv("WORKFLOWS_STATUS_POLL_SECONDS", "-5")
    assert worker._resolve_poll_interval() == 15.0


def test_poll_interval_falls_back_to_default_for_zero(monkeypatch):
    """A zero interval doesn't crash time.sleep(), but it removes the only
    throttle on the sweep loop entirely — an un-throttled, continuous
    hammering of the candidate query and every get_workflow_status call."""
    monkeypatch.setenv("WORKFLOWS_STATUS_POLL_SECONDS", "0")
    assert worker._resolve_poll_interval() == 15.0


def test_poll_interval_logs_a_warning_when_falling_back_for_a_bad_value(
    monkeypatch, caplog
):
    monkeypatch.setenv("WORKFLOWS_STATUS_POLL_SECONDS", "-5")
    with caplog.at_level("WARNING"):
        worker._resolve_poll_interval()
    assert any(
        "WORKFLOWS_STATUS_POLL_SECONDS" in record.message for record in caplog.records
    )
