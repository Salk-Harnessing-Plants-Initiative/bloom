"""Unit tests for the pipeline-trigger module's validation/enumeration/dedup/
batching/enqueue logic (no real DB/supabase client needed — a fake fluent client
is used, matching test_video.py's convention), plus two route-wiring tests for
main.py's `/pipeline` endpoint using FastAPI's `TestClient` + `dependency_overrides`
— the only way to prove `Depends(require_supabase_user)` is actually wired
correctly without duplicating auth.py's own already-covered test_auth.py logic.
"""

import pipeline
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

# --------------------------------------------------------------------------- #
# Fake supabase client — routes by table name; tracks every table() call so
# tests can assert call counts (e.g. "one batched query, not a per-scan loop").
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
        self._insert_payload = None

    def select(self, *a, **k):
        return self

    def eq(self, key, val):
        self._filters.append(("eq", key, val))
        return self

    def in_(self, key, vals):
        self._filters.append(("in_", key, list(vals)))
        return self

    def limit(self, *a, **k):
        return self

    def insert(self, payload):
        self._insert_payload = payload
        return self

    def execute(self):
        self._client.calls.append(self._table)
        if self._insert_payload is not None:
            return self._client._handle_insert(self._table, self._insert_payload)
        rows = self._client._table_rows(self._table)
        return _Result(_apply_filters(rows, self._filters))


class _Rpc:
    def __init__(self, client, name, params):
        self._client = client
        self._name = name
        self._params = params

    def execute(self):
        self._client.rpc_calls.append((self._name, self._params))
        return _Result(1)


class _FakeClient:
    """Seed via keyword args matching table names. `cyl_pipeline_runs` inserts get
    an auto-incrementing id; `cyl_pipeline_run_scans` inserts are recorded as-is
    (bulk insert accepts a list of dicts)."""

    def __init__(
        self,
        *,
        cyl_scans_extended=None,
        cyl_waves=None,
        cyl_experiments=None,
        cyl_scan_traits=None,
        cyl_trait_sources=None,
    ):
        self._data = {
            "cyl_scans_extended": cyl_scans_extended or [],
            "cyl_waves": cyl_waves or [],
            "cyl_experiments": cyl_experiments or [],
            "cyl_scan_traits": cyl_scan_traits or [],
            "cyl_trait_sources": cyl_trait_sources or [],
        }
        self.calls: list[str] = []
        self.rpc_calls: list[tuple] = []
        self.inserted_runs: list[dict] = []
        self.inserted_run_scans: list[dict] = []
        self._next_run_id = 1

    def _table_rows(self, table):
        return self._data.get(table, [])

    def table(self, name):
        return _Query(self, name)

    def rpc(self, name, params):
        return _Rpc(self, name, params)

    def _handle_insert(self, table, payload):
        if table == "cyl_pipeline_runs":
            row = dict(payload)
            row["id"] = self._next_run_id
            self._next_run_id += 1
            self.inserted_runs.append(row)
            return _Result([row])
        if table == "cyl_pipeline_run_scans":
            rows = payload if isinstance(payload, list) else [payload]
            self.inserted_run_scans.extend(rows)
            return _Result(rows)
        raise AssertionError(f"unexpected insert into {table}")


def _hash_of(params):
    from sleap_roots_contracts import compute_param_hash

    return compute_param_hash(params)


def _source(id_, param_hash):
    return {"id": id_, "metadata": {"params": {"param_hash": param_hash}}}


# --------------------------------------------------------------------------- #
# Request validation
# --------------------------------------------------------------------------- #


def test_rejects_malformed_json_body():
    with pytest.raises(HTTPException) as ei:
        pipeline.trigger_pipeline("not-a-dict", "user-1")
    assert ei.value.status_code == 422


def test_rejects_scan_ids_target_with_empty_list():
    body = {"target_level": "scan_ids", "target_id": None, "scan_ids": [], "params": {}}
    with pytest.raises(HTTPException) as ei:
        pipeline.trigger_pipeline(body, "user-1")
    assert ei.value.status_code == 422


def test_accepts_scan_ids_with_populated_list(monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "app_client",
        lambda: _FakeClient(
            cyl_scans_extended=[{"scan_id": 1}, {"scan_id": 2}, {"scan_id": 3}]
        ),
    )
    body = {
        "target_level": "scan_ids",
        "target_id": None,
        "scan_ids": [1, 2, 3],
        "params": {},
    }
    result = pipeline.trigger_pipeline(body, "user-1")
    assert result["scan_count"] == 3


def test_rejects_experiment_target_with_null_id():
    body = {"target_level": "experiment", "target_id": None, "params": {}}
    with pytest.raises(HTTPException) as ei:
        pipeline.trigger_pipeline(body, "user-1")
    assert ei.value.status_code == 422


def test_rejects_scan_ids_present_with_non_scan_ids_level():
    body = {
        "target_level": "experiment",
        "target_id": 5,
        "scan_ids": [1, 2],
        "params": {},
    }
    with pytest.raises(HTTPException) as ei:
        pipeline.trigger_pipeline(body, "user-1")
    assert ei.value.status_code == 422


@pytest.mark.parametrize("bad_id", ["5", 5.0])
def test_rejects_non_integer_target_id(bad_id):
    body = {"target_level": "experiment", "target_id": bad_id, "params": {}}
    with pytest.raises(HTTPException) as ei:
        pipeline.trigger_pipeline(body, "user-1")
    assert ei.value.status_code == 422


@pytest.mark.parametrize("bad_id", [0, -1])
def test_rejects_non_positive_target_id(bad_id):
    body = {"target_level": "experiment", "target_id": bad_id, "params": {}}
    with pytest.raises(HTTPException) as ei:
        pipeline.trigger_pipeline(body, "user-1")
    assert ei.value.status_code == 422


# --------------------------------------------------------------------------- #
# Enumeration
# --------------------------------------------------------------------------- #


def test_enumerate_scan_resolves_single_scan(monkeypatch):
    monkeypatch.setattr(
        pipeline, "app_client", lambda: _FakeClient(cyl_scans_extended=[{"scan_id": 7}])
    )
    body = {"target_level": "scan", "target_id": 7, "params": {}}
    result = pipeline.trigger_pipeline(body, "user-1")
    assert result["scan_count"] == 1


def test_enumerate_wave_resolves_via_cyl_scans_extended(monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "app_client",
        lambda: _FakeClient(
            cyl_waves=[{"id": 3}],
            cyl_scans_extended=[
                {"scan_id": 1, "wave_id": 3},
                {"scan_id": 2, "wave_id": 3},
            ],
        ),
    )
    body = {"target_level": "wave", "target_id": 3, "params": {}}
    result = pipeline.trigger_pipeline(body, "user-1")
    assert result["scan_count"] == 2


def test_enumerate_experiment_resolves_via_cyl_scans_extended(monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "app_client",
        lambda: _FakeClient(
            cyl_experiments=[{"id": 9}],
            cyl_scans_extended=[
                {"scan_id": 1, "experiment_id": 9},
                {"scan_id": 2, "experiment_id": 9},
                {"scan_id": 3, "experiment_id": 9},
            ],
        ),
    )
    body = {"target_level": "experiment", "target_id": 9, "params": {}}
    result = pipeline.trigger_pipeline(body, "user-1")
    assert result["scan_count"] == 3


def test_enumerate_scan_ids_resolves_exact_given_list(monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "app_client",
        lambda: _FakeClient(
            cyl_scans_extended=[{"scan_id": 4}, {"scan_id": 9}, {"scan_id": 15}]
        ),
    )
    body = {
        "target_level": "scan_ids",
        "target_id": None,
        "scan_ids": [4, 9, 15],
        "params": {},
    }
    result = pipeline.trigger_pipeline(body, "user-1")
    assert result["scan_count"] == 3


def test_enumerate_unknown_target_404(monkeypatch):
    monkeypatch.setattr(pipeline, "app_client", lambda: _FakeClient(cyl_experiments=[]))
    body = {"target_level": "experiment", "target_id": 404, "params": {}}
    with pytest.raises(HTTPException) as ei:
        pipeline.trigger_pipeline(body, "user-1")
    assert ei.value.status_code == 404


def test_enumerate_unknown_scan_id_in_scan_ids_404(monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "app_client",
        lambda: _FakeClient(cyl_scans_extended=[{"scan_id": 1}]),
    )
    body = {
        "target_level": "scan_ids",
        "target_id": None,
        "scan_ids": [1, 999],
        "params": {},
    }
    with pytest.raises(HTTPException) as ei:
        pipeline.trigger_pipeline(body, "user-1")
    assert ei.value.status_code == 404


def test_enumerate_existing_wave_with_zero_scans_succeeds_with_scan_count_zero(
    monkeypatch,
):
    monkeypatch.setattr(
        pipeline,
        "app_client",
        lambda: _FakeClient(cyl_waves=[{"id": 3}], cyl_scans_extended=[]),
    )
    body = {"target_level": "wave", "target_id": 3, "params": {}}
    result = pipeline.trigger_pipeline(body, "user-1")
    assert result["scan_count"] == 0


def test_enumerate_existing_experiment_with_zero_scans_succeeds_with_scan_count_zero(
    monkeypatch,
):
    monkeypatch.setattr(
        pipeline,
        "app_client",
        lambda: _FakeClient(cyl_experiments=[{"id": 9}], cyl_scans_extended=[]),
    )
    body = {"target_level": "experiment", "target_id": 9, "params": {}}
    result = pipeline.trigger_pipeline(body, "user-1")
    assert result["scan_count"] == 0


# --------------------------------------------------------------------------- #
# Dedup preview — informational only, checks ALL sources, one batched query
# --------------------------------------------------------------------------- #


def test_dedup_preview_counts_matching_prior_source_but_still_enqueues_it(monkeypatch):
    params = {"age": 14}
    h = _hash_of(params)
    client = _FakeClient(
        cyl_scans_extended=[{"scan_id": 1}],
        cyl_scan_traits=[{"scan_id": 1, "source_id": 100}],
        cyl_trait_sources=[_source(100, h)],
    )
    monkeypatch.setattr(pipeline, "app_client", lambda: client)
    body = {"target_level": "scan", "target_id": 1, "params": params}
    result = pipeline.trigger_pipeline(body, "user-1")
    assert result["reused_count"] == 1
    assert result["scan_count"] == 1
    assert client.inserted_run_scans[0]["status"] == "queued"
    assert len(client.rpc_calls) == 1  # still enqueued despite the dedup-preview match


def test_dedup_preview_finds_older_matching_source_when_newest_source_has_different_params(
    monkeypatch,
):
    older_hash = _hash_of({"age": 14})
    newer_hash = _hash_of({"age": 21})
    client = _FakeClient(
        cyl_scans_extended=[{"scan_id": 1}],
        cyl_scan_traits=[
            {"scan_id": 1, "source_id": 100},
            {"scan_id": 1, "source_id": 200},
        ],
        cyl_trait_sources=[_source(100, older_hash), _source(200, newer_hash)],
    )
    monkeypatch.setattr(pipeline, "app_client", lambda: client)
    body = {"target_level": "scan", "target_id": 1, "params": {"age": 14}}
    result = pipeline.trigger_pipeline(body, "user-1")
    assert result["reused_count"] == 1


def test_dedup_preview_counts_scan_once_even_with_two_matching_sources(monkeypatch):
    h = _hash_of({"age": 14})
    client = _FakeClient(
        cyl_scans_extended=[{"scan_id": 1}],
        cyl_scan_traits=[
            {"scan_id": 1, "source_id": 100},
            {"scan_id": 1, "source_id": 200},
        ],
        cyl_trait_sources=[_source(100, h), _source(200, h)],
    )
    monkeypatch.setattr(pipeline, "app_client", lambda: client)
    body = {"target_level": "scan", "target_id": 1, "params": {"age": 14}}
    result = pipeline.trigger_pipeline(body, "user-1")
    assert result["reused_count"] == 1


def test_dedup_preview_excludes_scan_with_no_prior_source_from_reused_count(
    monkeypatch,
):
    client = _FakeClient(cyl_scans_extended=[{"scan_id": 1}])
    monkeypatch.setattr(pipeline, "app_client", lambda: client)
    body = {"target_level": "scan", "target_id": 1, "params": {"age": 14}}
    result = pipeline.trigger_pipeline(body, "user-1")
    assert result["reused_count"] == 0


def test_dedup_preview_excludes_scan_whose_sources_all_have_differing_params(
    monkeypatch,
):
    other_hash = _hash_of({"age": 99})
    client = _FakeClient(
        cyl_scans_extended=[{"scan_id": 1}],
        cyl_scan_traits=[{"scan_id": 1, "source_id": 100}],
        cyl_trait_sources=[_source(100, other_hash)],
    )
    monkeypatch.setattr(pipeline, "app_client", lambda: client)
    body = {"target_level": "scan", "target_id": 1, "params": {"age": 14}}
    result = pipeline.trigger_pipeline(body, "user-1")
    assert result["reused_count"] == 0


def test_all_scans_matching_prior_source_still_all_enqueued_not_short_circuited(
    monkeypatch,
):
    h = _hash_of({"age": 14})
    client = _FakeClient(
        cyl_scans_extended=[{"scan_id": 1}, {"scan_id": 2}],
        cyl_scan_traits=[
            {"scan_id": 1, "source_id": 100},
            {"scan_id": 2, "source_id": 101},
        ],
        cyl_trait_sources=[_source(100, h), _source(101, h)],
    )
    monkeypatch.setattr(pipeline, "app_client", lambda: client)
    body = {
        "target_level": "scan_ids",
        "target_id": None,
        "scan_ids": [1, 2],
        "params": {"age": 14},
    }
    result = pipeline.trigger_pipeline(body, "user-1")
    assert result["reused_count"] == 2
    assert result["scan_count"] == 2
    assert client.inserted_runs[0]["status"] == "queued"
    assert len(client.inserted_run_scans) == 2
    assert len(client.rpc_calls) == 1


def test_dedup_preview_issues_one_batched_query_not_a_per_scan_loop(monkeypatch):
    h = _hash_of({})

    def _run_with(n_scans):
        scans = [{"scan_id": i} for i in range(1, n_scans + 1)]
        # At least one scan has a prior source, so both queries (cyl_scan_traits,
        # then cyl_trait_sources for the resulting source_ids) actually run —
        # otherwise the second query is correctly skipped entirely (no candidate
        # source_ids), which would make this assertion vacuous.
        client = _FakeClient(
            cyl_scans_extended=scans,
            cyl_scan_traits=[{"scan_id": 1, "source_id": 100}],
            cyl_trait_sources=[_source(100, h)],
        )
        monkeypatch.setattr(pipeline, "app_client", lambda c=client: c)
        body = {
            "target_level": "scan_ids",
            "target_id": None,
            "scan_ids": [s["scan_id"] for s in scans],
            "params": {},
        }
        pipeline.trigger_pipeline(body, "user-1")
        return client.calls.count("cyl_scan_traits") + client.calls.count(
            "cyl_trait_sources"
        )

    small = _run_with(3)
    large = _run_with(30)
    assert small == large == 2  # exactly one query per table, regardless of scan count


# --------------------------------------------------------------------------- #
# Row writing
# --------------------------------------------------------------------------- #


def test_writes_run_and_scan_rows_before_enqueue(monkeypatch):
    h = _hash_of({"age": 14})
    scans = [{"scan_id": i} for i in range(1, 41)]
    matched = {"scan_id": 1, "source_id": 100}
    client = _FakeClient(
        cyl_scans_extended=scans,
        cyl_scan_traits=[matched, {"scan_id": 2, "source_id": 101}],
        cyl_trait_sources=[_source(100, h), _source(101, h)],
    )
    monkeypatch.setattr(pipeline, "app_client", lambda: client)
    body = {
        "target_level": "scan_ids",
        "target_id": None,
        "scan_ids": [s["scan_id"] for s in scans],
        "params": {"age": 14},
    }
    result = pipeline.trigger_pipeline(body, "user-1")
    assert len(client.inserted_runs) == 1
    assert client.inserted_runs[0]["scan_count"] == 40
    assert result["reused_count"] == 2
    assert len(client.inserted_run_scans) == 40
    assert all(row["status"] == "queued" for row in client.inserted_run_scans)


def test_zero_scan_target_writes_run_row_with_scan_count_zero_and_completes(
    monkeypatch,
):
    client = _FakeClient(cyl_experiments=[{"id": 9}], cyl_scans_extended=[])
    monkeypatch.setattr(pipeline, "app_client", lambda: client)
    body = {"target_level": "experiment", "target_id": 9, "params": {}}
    pipeline.trigger_pipeline(body, "user-1")
    assert len(client.inserted_runs) == 1
    assert client.inserted_runs[0]["scan_count"] == 0
    assert client.inserted_runs[0]["status"] == "complete"
    assert len(client.inserted_run_scans) == 0
    assert len(client.rpc_calls) == 0


# --------------------------------------------------------------------------- #
# Batching
# --------------------------------------------------------------------------- #


def test_chunks_all_scans_into_batch_size_groups_and_enqueues_each(monkeypatch):
    monkeypatch.setattr(pipeline, "BATCH_SIZE", 25)
    scans = [{"scan_id": i} for i in range(1, 93)]  # 92 scans
    client = _FakeClient(cyl_scans_extended=scans)
    monkeypatch.setattr(pipeline, "app_client", lambda: client)
    body = {
        "target_level": "scan_ids",
        "target_id": None,
        "scan_ids": [s["scan_id"] for s in scans],
        "params": {},
    }
    pipeline.trigger_pipeline(body, "user-1")
    assert len(client.rpc_calls) == 4
    batch_indices = [row["batch_index"] for row in client.inserted_run_scans]
    counts = {i: batch_indices.count(i) for i in range(4)}
    assert counts == {0: 25, 1: 25, 2: 25, 3: 17}


def test_exact_multiple_of_batch_size_produces_no_empty_trailing_batch(monkeypatch):
    monkeypatch.setattr(pipeline, "BATCH_SIZE", 25)
    scans = [{"scan_id": i} for i in range(1, 51)]  # exactly 50 scans
    client = _FakeClient(cyl_scans_extended=scans)
    monkeypatch.setattr(pipeline, "app_client", lambda: client)
    body = {
        "target_level": "scan_ids",
        "target_id": None,
        "scan_ids": [s["scan_id"] for s in scans],
        "params": {},
    }
    pipeline.trigger_pipeline(body, "user-1")
    assert len(client.rpc_calls) == 2


# --------------------------------------------------------------------------- #
# Response shape
# --------------------------------------------------------------------------- #


def test_success_response_shape(monkeypatch):
    client = _FakeClient(cyl_scans_extended=[{"scan_id": 1}])
    monkeypatch.setattr(pipeline, "app_client", lambda: client)
    body = {"target_level": "scan", "target_id": 1, "params": {}}
    result = pipeline.trigger_pipeline(body, "user-1")
    assert set(result) == {"pipeline_run_id", "scan_count", "reused_count"}
    assert result["pipeline_run_id"] == client.inserted_runs[0]["id"]


# --------------------------------------------------------------------------- #
# Route wiring — auth + rate limit, via TestClient + dependency_overrides.
# This is the only reliable way to prove Depends(require_supabase_user) is
# actually wired into the /pipeline route: calling main's route function
# directly bypasses FastAPI's dependency-injection machinery entirely, so a
# monkeypatch on the imported name would prove nothing. require_supabase_user's
# own 401/403 behavior is already exhaustively covered by test_auth.py — these
# two tests only prove the route's wiring, not re-test that logic.
# --------------------------------------------------------------------------- #


def test_pipeline_route_401_without_auth(monkeypatch):
    import main
    from auth import require_supabase_user

    def _raise_401():
        raise HTTPException(status_code=401, detail="missing token")

    called = {"n": 0}
    monkeypatch.setattr(
        pipeline,
        "trigger_pipeline",
        lambda body, user_id: called.__setitem__("n", called["n"] + 1),
    )
    main.app.dependency_overrides[require_supabase_user] = _raise_401
    try:
        resp = TestClient(main.app).post(
            "/pipeline", json={"target_level": "scan", "target_id": 1, "params": {}}
        )
        assert resp.status_code == 401
        assert called["n"] == 0
    finally:
        main.app.dependency_overrides.clear()


def test_pipeline_route_429_before_any_work(monkeypatch):
    import main
    from auth import require_supabase_user

    main.app.dependency_overrides[require_supabase_user] = lambda: "user-1"
    called = {"n": 0}
    monkeypatch.setattr(
        pipeline,
        "trigger_pipeline",
        lambda body, user_id: called.__setitem__("n", called["n"] + 1),
    )

    def _raise_429(user_id):
        raise HTTPException(
            status_code=429, detail="rate limited", headers={"Retry-After": "60"}
        )

    monkeypatch.setattr(main, "enforce_rate_limit", _raise_429)
    try:
        resp = TestClient(main.app).post(
            "/pipeline", json={"target_level": "scan", "target_id": 1, "params": {}}
        )
        assert resp.status_code == 429
        assert resp.headers.get("Retry-After") == "60"
        assert called["n"] == 0  # trigger_pipeline never ran — rate limit gates first
    finally:
        main.app.dependency_overrides.clear()
