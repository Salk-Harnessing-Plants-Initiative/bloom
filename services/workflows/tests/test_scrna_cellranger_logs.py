"""Unit tests for reading a Cell Ranger step's log: step and pod checks, the read from the
run-logs bucket, a log not saved yet, storage errors, truncation and the route (no database
or storage)."""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import scrna_cellranger_logs as logs
USER = "00000000-0000-0000-0000-000000000001"
WORKFLOW = "scrna-cellranger-dev-7-abcd1234"
POD = f"{WORKFLOW}-count-3938252481"
ROW = {
    "id": 7,
    "argo_workflow_name": WORKFLOW,
    "step_pods": {
        "stage": f"{WORKFLOW}-stage-sample-1",
        "count": POD,
    },
}


class StorageError(Exception):
    """Stands in for storage3's StorageApiError, which carries the HTTP status."""

    def __init__(self, message, status, code="not_found"):
        super().__init__(message)
        self.status = status
        self.code = code


class _Result:
    def __init__(self, data):
        self.data = data


class FakeSupabase:
    """Serves `rows` filtered by the .eq() calls, and records them; its storage serves
    log_state["result"] (text, or an exception to raise) and records each download."""

    log_state = {"result": "", "calls": []}

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.filters = []
        self.selected = []

    @property
    def storage(self):
        state = self.log_state

        class _Bucket:
            def __init__(self, name):
                self.name = name

            def download(self, path):
                state["calls"].append((self.name, path))
                if isinstance(state["result"], Exception):
                    raise state["result"]
                return state["result"].encode()

        class _Storage:
            def from_(self, name):
                return _Bucket(name)

        return _Storage()

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
    """The stored log the fake storage serves; `result` is returned, or raised."""
    state = {"result": "line 1\nline 2\n", "calls": []}
    monkeypatch.setattr(FakeSupabase, "log_state", state)
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
    assert pod_log["calls"] == [("run-logs", f"scrna/{WORKFLOW}/count.log")]


def test_a_steps_log_is_named_for_its_template():
    assert logs._template(f"{WORKFLOW}-stage-sample-1", WORKFLOW) == "stage-sample"
    assert logs._template(f"{WORKFLOW}-find-annotation-42", WORKFLOW) == "find-annotation"


def test_only_cellranger_runs_are_looked_up(db, pod_log):
    logs.read_step_log(7, "count")
    assert "step_pods" in db.selected[0] and "argo_workflow_name" in db.selected[0]
    assert ("workflow_type", "scrna-cellranger") in db.filters[0]
    assert ("id", 7) in db.filters[0]


@pytest.mark.parametrize("step", ["align", "", None, "Count", "stage-sample"])
def test_an_unknown_step_is_refused_before_any_lookup(monkeypatch, step):
    monkeypatch.setattr(logs, "app_client", lambda: pytest.fail("DB was called"))
    status, detail = _status(7, step)
    assert status == 422
    assert (
        "fetch-sra, stage, qc, download-genome, build-reference, annotate-genome, count, "
        "preprocess, cluster, de, build-h5ad, load-dataset, go-pathways, cleanup, "
        "stage-reference" in detail
    )


def test_every_cellranger_step_can_be_asked_for():
    assert logs.STEPS == (
        "fetch-sra",
        "stage",
        "qc",
        "download-genome",
        "build-reference",
        "annotate-genome",
        "count",
        "preprocess",
        "cluster",
        "de",
        "build-h5ad",
        "load-dataset",
        "go-pathways",
        "cleanup",
        "stage-reference",
    ), "an earlier run's retired step's log can still be read"


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
        lambda: FakeSupabase([{"id": 7, "argo_workflow_name": WORKFLOW,
                               "step_pods": {"count": pod}}]),
    )
    assert _status(7, "count")[0] == 500
    assert pod_log["calls"] == []


def test_a_pod_of_another_workflow_is_never_read(monkeypatch, pod_log):
    monkeypatch.setattr(
        logs,
        "app_client",
        lambda: FakeSupabase([{"id": 7, "argo_workflow_name": "other-wf",
                               "step_pods": {"count": POD}}]),
    )
    assert _status(7, "count")[0] == 500
    assert pod_log["calls"] == []


@pytest.mark.parametrize("status, code", [("404", "not_found"), (404, "x"), (400, "not_found")])
def test_a_log_not_saved_yet_is_404(db, pod_log, status, code):
    pod_log["result"] = StorageError("Object not found", status, code)
    code, detail = _status(7, "count")
    assert code == 404
    assert detail == (
        "Step count of run 7 has no log in Bloom yet; a running step saves its log every "
        "30 seconds"
    )


@pytest.mark.parametrize("error", [StorageError("secret-token boom", "500", "InternalError"),
                                   StorageError("Unable to parse", 400, "InternalError"),
                                   OSError("connection reset")])
def test_storage_errors_are_reported_without_detail(db, pod_log, error):
    pod_log["result"] = error
    code, detail = _status(7, "count")
    assert code == 502
    assert "secret-token" not in detail and "reset" not in detail


def test_a_log_at_the_line_limit_is_kept_whole(db, pod_log):
    pod_log["result"] = "x\n" * logs.TAIL_LINES
    out = logs.read_step_log(7, "count")
    assert out["truncated"] is False
    assert out["log"].count("\n") == logs.TAIL_LINES


def test_a_log_past_the_line_limit_keeps_its_last_lines(db, pod_log):
    pod_log["result"] = "first\n" + "x\n" * logs.TAIL_LINES
    out = logs.read_step_log(7, "count")
    assert out["truncated"] is True
    assert "first" not in out["log"]
    assert out["log"].count("\n") == logs.TAIL_LINES


def test_a_log_at_the_size_limit_is_kept_whole(db, pod_log):
    pod_log["result"] = "y" * logs.LIMIT_BYTES
    out = logs.read_step_log(7, "count")
    assert out["truncated"] is False
    assert len(out["log"]) == logs.LIMIT_BYTES


def test_a_log_over_the_size_limit_keeps_its_end(db, pod_log):
    # The end is where the error is.
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
