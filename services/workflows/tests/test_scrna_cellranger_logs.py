"""Unit tests for reading a Cell Ranger step's log: step and pod checks, the pod log
read, a gone pod, cluster errors, truncation and the route (no database or cluster)."""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import scrna_cellranger_logs as logs
from k8s_client import K8sConfigError, K8sPodNotRunningError, K8sStatusError

USER = "00000000-0000-0000-0000-000000000001"
POD = "scrna-cellranger-dev-7-abcd1234-count-3938252481"
ROW = {
    "id": 7,
    "step_pods": {
        "stage": "scrna-cellranger-dev-7-abcd1234-stage-sample-1",
        "count": POD,
    },
}


class _Result:
    def __init__(self, data):
        self.data = data


class FakeSupabase:
    """Serves `rows` filtered by the .eq() calls, and records them."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.filters = []
        self.selected = []

    def table(self, name):
        client = self

        class _Query:
            def __init__(self):
                self.eqs = []

            def select(self, cols):
                client.selected.append(cols)
                return self

            def eq(self, col, value):
                self.eqs.append((col, value))
                return self

            def limit(self, n):
                return self

            def execute(self):
                client.filters.append(self.eqs)
                matched = [
                    r
                    for r in client.rows
                    if all(
                        r.get(c, logs.WORKFLOW_TYPE if c == "workflow_type" else None)
                        == v
                        for c, v in self.eqs
                    )
                ]
                return _Result(matched)

        return _Query()


@pytest.fixture
def db(monkeypatch):
    fake = FakeSupabase([ROW])
    monkeypatch.setattr(logs, "app_client", lambda: fake)
    return fake


@pytest.fixture
def pod_log(monkeypatch):
    """Records get_pod_log calls; `result` is returned, or raised if an exception."""
    state = {"result": "line 1\nline 2\n", "calls": []}

    def _get(pod, container, tail_lines, limit_bytes):
        state["calls"].append((pod, container, tail_lines, limit_bytes))
        if isinstance(state["result"], Exception):
            raise state["result"]
        return state["result"]

    monkeypatch.setattr(logs, "get_pod_log", _get)
    return state


def _status(run_id, step):
    with pytest.raises(HTTPException) as exc:
        logs.read_step_log(run_id, step)
    return exc.value.status_code, exc.value.detail


# --------------------------------------------------------------------------- #
# read_step_log
# --------------------------------------------------------------------------- #


def test_a_started_steps_log_is_returned(db, pod_log):
    assert logs.read_step_log(7, "count") == {
        "run_id": 7,
        "step": "count",
        "pod": POD,
        "log": "line 1\nline 2\n",
        "truncated": False,
    }
    assert pod_log["calls"] == [(POD, "main", 2000, 4 * 1024 * 1024)]


def test_only_cellranger_runs_are_looked_up(db, pod_log):
    logs.read_step_log(7, "count")
    assert "step_pods" in db.selected[0]
    assert ("workflow_type", "scrna-cellranger") in db.filters[0]
    assert ("id", 7) in db.filters[0]


@pytest.mark.parametrize("step", ["align", "", None, "Count", "stage-sample"])
def test_an_unknown_step_is_refused_before_any_lookup(monkeypatch, step):
    monkeypatch.setattr(logs, "app_client", lambda: pytest.fail("DB was called"))
    status, detail = _status(7, step)
    assert status == 422
    assert (
        "fetch-sra, stage-reference, stage, qc, count, preprocess, cluster, build-h5ad, cleanup"
        in detail
    )


def test_every_cellranger_step_can_be_asked_for():
    assert logs.STEPS == (
        "fetch-sra",
        "stage-reference",
        "stage",
        "qc",
        "count",
        "preprocess",
        "cluster",
        "build-h5ad",
        "cleanup",
    )


def test_an_unknown_run_is_404(db, pod_log):
    assert _status(99, "count") == (404, "Cell Ranger run 99 not found")


def test_a_step_that_has_not_started_is_404(db, pod_log):
    assert _status(7, "cleanup") == (404, "Step cleanup of run 7 has not started")
    assert pod_log["calls"] == []


def test_a_run_with_no_pods_yet_is_404(monkeypatch, pod_log):
    monkeypatch.setattr(
        logs, "app_client", lambda: FakeSupabase([{"id": 7, "step_pods": None}])
    )
    assert _status(7, "stage")[0] == 404


@pytest.mark.parametrize("pod", ["../../secrets", "Pod_Name", "a/b", "x" * 254, 5])
def test_a_pod_name_kubernetes_would_not_accept_is_never_requested(
    monkeypatch, pod_log, pod
):
    monkeypatch.setattr(
        logs,
        "app_client",
        lambda: FakeSupabase([{"id": 7, "step_pods": {"count": pod}}]),
    )
    assert _status(7, "count")[0] == 500
    assert pod_log["calls"] == []


def test_a_removed_pod_is_410(db, pod_log):
    pod_log["result"] = None
    status, detail = _status(7, "count")
    assert status == 410
    assert "no longer available" in detail


@pytest.mark.parametrize(
    "error, status",
    [(K8sStatusError("x"), 502), (K8sConfigError("no token"), 503)],
)
def test_cluster_errors_are_reported_without_detail(db, pod_log, error, status):
    pod_log["result"] = error
    code, detail = _status(7, "count")
    assert code == status
    assert "no token" not in detail


def test_a_log_at_the_line_limit_is_marked_cut(db, pod_log):
    pod_log["result"] = "x\n" * logs.TAIL_LINES
    assert logs.read_step_log(7, "count")["truncated"] is True


def test_a_log_at_the_size_limit_is_kept_whole(db, pod_log):
    pod_log["result"] = "y" * logs.LIMIT_BYTES
    out = logs.read_step_log(7, "count")
    assert out["truncated"] is False
    assert len(out["log"]) == logs.LIMIT_BYTES


def test_a_log_over_the_size_limit_keeps_its_end(db, pod_log):
    # The kubelet's byte cap would keep the start; the route keeps the end, where the error is.
    line = "progress " * 100 + "\n"
    pod_log["result"] = line * 3000 + "ERROR: cellranger count failed\n"
    out = logs.read_step_log(7, "count")
    assert out["truncated"] is True
    assert out["log"].endswith("ERROR: cellranger count failed\n")
    assert len(out["log"].encode()) <= logs.LIMIT_BYTES
    assert out["log"].startswith("progress ")


def test_the_end_is_cut_at_a_whole_character(db, pod_log):
    pod_log["result"] = "é" * logs.LIMIT_BYTES
    out = logs.read_step_log(7, "count")
    assert "\ufffd" not in out["log"]
    assert out["truncated"] is True


def test_a_step_waiting_to_run_is_409(db, pod_log):
    pod_log["result"] = K8sPodNotRunningError("waiting")
    status, detail = _status(7, "count")
    assert status == 409
    assert detail == (
        "Step count of run 7 hasn't started running yet; its log appears once it does"
    )


# --------------------------------------------------------------------------- #
# The route
# --------------------------------------------------------------------------- #


@pytest.fixture
def app_as_user():
    import main
    from auth import require_supabase_user

    main.app.dependency_overrides[require_supabase_user] = lambda: USER
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def test_the_route_returns_the_log(app_as_user, db, pod_log):
    resp = app_as_user.get("/scrna/cellranger/runs/7/logs", params={"step": "count"})
    assert resp.status_code == 200
    assert resp.json()["pod"] == POD


def test_the_route_needs_a_step(app_as_user, db, pod_log):
    assert app_as_user.get("/scrna/cellranger/runs/7/logs").status_code == 422


def test_the_route_needs_a_login(monkeypatch):
    import main
    from auth import require_supabase_user

    def _raise_401():
        raise HTTPException(status_code=401, detail="missing token")

    monkeypatch.setattr(logs, "read_step_log", lambda *a: pytest.fail("ran"))
    main.app.dependency_overrides[require_supabase_user] = _raise_401
    try:
        resp = TestClient(main.app).get(
            "/scrna/cellranger/runs/7/logs", params={"step": "count"}
        )
        assert resp.status_code == 401
    finally:
        main.app.dependency_overrides.clear()


def test_the_route_is_rate_limited(app_as_user, db, pod_log, monkeypatch):
    import auth

    monkeypatch.setattr(auth, "RATE_LIMIT", 1)
    url, params = "/scrna/cellranger/runs/7/logs", {"step": "count"}
    assert app_as_user.get(url, params=params).status_code == 200
    assert app_as_user.get(url, params=params).status_code == 429
    assert len(pod_log["calls"]) == 1
