"""Route-boundary tests: scan/experiment identifiers are validated (422) before the
enqueue RPC or the DB is ever touched — the cheap input-cap layer at the API edge."""

import pytest
from fastapi.testclient import TestClient

import main
from auth import require_supabase_user

BIGINT_OVERFLOW = 9223372036854775808  # Postgres bigint max (9223372036854775807) + 1


@pytest.fixture
def client(monkeypatch):
    # Bypass auth + rate limit so the assertions isolate path-parameter validation.
    main.app.dependency_overrides[require_supabase_user] = lambda: "test-user"
    monkeypatch.setattr(main, "enforce_rate_limit", lambda user_id: None)
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


ROUTES = [
    "/cyl/experiments/{e}/scans/{s}/video",
    "/cyl/experiments/{e}/scans/{s}/video/queue",
]

# non-positive or beyond-bigint ids on either identifier
BAD_IDS = [(0, 1), (-1, 1), (1, 0), (1, -5), (BIGINT_OVERFLOW, 1), (1, BIGINT_OVERFLOW)]


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("e,s", BAD_IDS)
def test_rejects_out_of_range_ids(client, monkeypatch, route, e, s):
    # An out-of-range id is rejected at the boundary; the render/enqueue code never runs.
    reached = {"enqueue": False, "render": False}
    monkeypatch.setattr(
        main,
        "enqueue_experiment_scan_video",
        lambda *a: reached.update(enqueue=True) or {},
    )
    monkeypatch.setattr(
        main,
        "generate_experiment_scan_video",
        lambda *a: reached.update(render=True) or {},
    )
    resp = client.post(route.format(e=e, s=s))
    assert resp.status_code == 422
    assert reached == {"enqueue": False, "render": False}


def test_accepts_valid_ids_queue(client, monkeypatch):
    # A well-formed request passes validation and reaches the enqueue helper.
    monkeypatch.setattr(
        main,
        "enqueue_experiment_scan_video",
        lambda e, s: {"job_id": "j1", "status": "queued"},
    )
    resp = client.post("/cyl/experiments/3/scans/5/video/queue")
    assert resp.status_code == 200
    assert resp.json() == {
        "experiment_id": 3,
        "scan_id": 5,
        "job_id": "j1",
        "status": "queued",
    }
