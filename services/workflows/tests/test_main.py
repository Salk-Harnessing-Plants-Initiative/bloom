"""Route-wiring tests for main.py's `GET /workflows/runs/{run_id}` endpoint
(registered here as `/runs/{run_id}` — Caddy strips the `/workflows` prefix
before proxying, matching every other route in this file), using FastAPI's
`TestClient` + `dependency_overrides` — the only reliable way to prove
`Depends(require_supabase_user)`/`enforce_rate_limit` are actually wired into
this route, matching test_pipeline.py's own route-wiring test convention.
`pipeline.get_run`'s own behavior (the DB read, the 404) is exercised
directly, not through the route, by mocking it here."""

import pipeline
from fastapi import HTTPException
from fastapi.testclient import TestClient


def test_get_run_returns_run_and_scans(monkeypatch):
    import main
    from auth import require_supabase_user

    main.app.dependency_overrides[require_supabase_user] = lambda: "user-1"
    payload = {
        "run": {"id": 42, "status": "running"},
        "scans": [{"id": 1, "scan_id": 100, "status": "queued"}],
    }
    monkeypatch.setattr(pipeline, "get_run", lambda run_id: payload)
    try:
        resp = TestClient(main.app).get("/runs/42")
        assert resp.status_code == 200
        assert resp.json() == payload
    finally:
        main.app.dependency_overrides.clear()


def test_get_run_404s_for_unknown_run_id(monkeypatch):
    import main
    from auth import require_supabase_user

    main.app.dependency_overrides[require_supabase_user] = lambda: "user-1"

    def _raise_404(run_id):
        raise HTTPException(status_code=404, detail=f"pipeline run {run_id} not found")

    monkeypatch.setattr(pipeline, "get_run", _raise_404)
    try:
        resp = TestClient(main.app).get("/runs/999")
        assert resp.status_code == 404
    finally:
        main.app.dependency_overrides.clear()


def test_get_run_requires_auth(monkeypatch):
    import main
    from auth import require_supabase_user

    def _raise_401():
        raise HTTPException(status_code=401, detail="missing token")

    called = {"n": 0}
    monkeypatch.setattr(
        pipeline, "get_run", lambda run_id: called.__setitem__("n", called["n"] + 1)
    )
    main.app.dependency_overrides[require_supabase_user] = _raise_401
    try:
        resp = TestClient(main.app).get("/runs/1")
        assert resp.status_code == 401
        assert called["n"] == 0
    finally:
        main.app.dependency_overrides.clear()


def test_get_run_rate_limited_returns_429(monkeypatch):
    import main
    from auth import require_supabase_user

    main.app.dependency_overrides[require_supabase_user] = lambda: "user-1"
    called = {"n": 0}
    monkeypatch.setattr(
        pipeline, "get_run", lambda run_id: called.__setitem__("n", called["n"] + 1)
    )

    def _raise_429(user_id):
        raise HTTPException(
            status_code=429, detail="rate limited", headers={"Retry-After": "60"}
        )

    monkeypatch.setattr(main, "enforce_rate_limit", _raise_429)
    try:
        resp = TestClient(main.app).get("/runs/1")
        assert resp.status_code == 429
        assert resp.headers.get("Retry-After") == "60"
        assert called["n"] == 0  # get_run never ran — rate limit gates first
    finally:
        main.app.dependency_overrides.clear()
