"""Route-wiring tests for main.py's `GET /workflows/runs/{run_id}` endpoint
(registered here as `/runs/{run_id}` — Caddy strips the `/workflows` prefix
before proxying, matching every other route in this file), using FastAPI's
`TestClient` + `dependency_overrides` — the only reliable way to prove
`Depends(require_supabase_user)`/`enforce_rate_limit` are actually wired into
this route, matching test_pipeline.py's own route-wiring test convention.
`pipeline.get_run`'s own behavior (the DB read, the 404) is exercised
directly, not through the route, by mocking it here."""

import time

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


# --- the video routes --------------------------------------------------------
#
# Both queue a render and answer 202 with the job. The handler must not be
# reached when auth or the rate limit refuses: a 401 or a 429 alone proves only
# what the client was told.

import pytest  # noqa: E402
from postgrest import APIError  # noqa: E402


@pytest.fixture
def signed_in(monkeypatch):
    import main
    from auth import require_supabase_user

    monkeypatch.setattr(main, "enforce_rate_limit", lambda user_id: None)
    main.app.dependency_overrides[require_supabase_user] = lambda: "user-1"
    yield main
    main.app.dependency_overrides.clear()


def test_plate_video_route_answers_202_with_the_job(monkeypatch, signed_in):
    import plate_request

    seen = []
    monkeypatch.setattr(
        plate_request,
        "enqueue",
        lambda experiment_id, body, user_id: seen.append((experiment_id, body, user_id))
        or {"job_id": "job-1", "created": True},
    )
    resp = TestClient(signed_in.app).post(
        "/gravi/experiments/12/plate-video", json={"plate_id": "P7", "wave_number": 1}
    )
    assert resp.status_code == 202
    assert resp.json() == {"job_id": "job-1", "created": True}
    assert seen == [(12, {"plate_id": "P7", "wave_number": 1}, "user-1")]


def test_a_refused_plate_video_is_logged(monkeypatch, signed_in, caplog):
    """The answer a caller sees when the button seems to do nothing leaves a record."""
    import plate_request

    def refuse(experiment_id, body, user_id):
        raise HTTPException(status_code=400, detail="plate_id must be 1-64 characters")

    monkeypatch.setattr(plate_request, "enqueue", refuse)
    with caplog.at_level("INFO", logger="main"):
        resp = TestClient(signed_in.app).post(
            "/gravi/experiments/12/plate-video",
            json={"plate_id": "P7", "wave_number": 1},
        )

    assert resp.status_code == 400
    assert "experiment 12" in caplog.text
    assert "'P7'" in caplog.text
    assert "user-1" in caplog.text


def test_a_rejected_body_is_logged_without_being_echoed_back(signed_in, caplog):
    """A plate id is caller-supplied text going into a log a person reads. A
    newline in it would put a second, invented line under a real one."""
    with caplog.at_level("INFO", logger="main"):
        resp = TestClient(signed_in.app).post(
            "/gravi/experiments/12/plate-video",
            json={"plate_id": "P7\nplate video for experiment 99: rendered"},
        )

    assert resp.status_code == 400
    assert "\\n" in caplog.text, "the newline reached the log unescaped"
    assert "\nplate video for experiment 99" not in caplog.text


def test_the_plate_progress_route_is_gone(signed_in):
    """Progress is read from the job row now; the in-memory route would answer nothing."""
    resp = TestClient(signed_in.app).get(
        "/gravi/experiments/12/plate-video/progress", params={"plate_id": "P7"}
    )
    assert resp.status_code in (404, 405)


def _cyl_stubs(monkeypatch, *, belongs=True, enqueue=None):
    import main

    calls = []
    monkeypatch.setattr(main, "app_client", lambda **kwargs: object())
    monkeypatch.setattr(main, "scan_in_experiment", lambda c, e, s: belongs)

    def default(client, queue, params):
        calls.append((queue.enqueue, params))
        return {"job_id": "job-9", "created": False}

    monkeypatch.setattr(main, "enqueue", enqueue or default)
    return calls


def test_cyl_video_route_answers_202_with_the_job(monkeypatch, signed_in):
    calls = _cyl_stubs(monkeypatch)
    resp = TestClient(signed_in.app).post("/cyl/experiments/3/scans/44/video")
    assert resp.status_code == 202
    assert resp.json() == {"job_id": "job-9", "created": False}
    assert calls == [
        (
            "enqueue_cyl_video",
            {"p_scan_id": 44, "p_experiment_id": 3, "p_requested_by": "user-1"},
        )
    ]


def test_cyl_video_route_404s_a_scan_outside_the_experiment(monkeypatch, signed_in):
    calls = _cyl_stubs(monkeypatch, belongs=False)
    resp = TestClient(signed_in.app).post("/cyl/experiments/3/scans/44/video")
    assert resp.status_code == 404
    assert calls == [], "a scan outside the experiment was queued"


def test_cyl_video_route_404s_when_the_database_says_the_scan_is_elsewhere(
    monkeypatch, signed_in
):
    """The membership check and the enqueue can disagree only in a race; the
    database's own answer is the same 404."""

    def elsewhere(client, queue, params):
        raise APIError(
            {"code": "23503", "message": "scan 44 does not belong to experiment 3"}
        )

    _cyl_stubs(monkeypatch, enqueue=elsewhere)
    resp = TestClient(signed_in.app).post("/cyl/experiments/3/scans/44/video")
    assert resp.status_code == 404


def test_cyl_video_route_409s_a_job_filed_under_another_experiment(
    monkeypatch, signed_in
):
    def mismatch(client, queue, params):
        raise APIError(
            {
                "code": "23514",
                "message": "scan 44 has an active video job under experiment 2",
            }
        )

    _cyl_stubs(monkeypatch, enqueue=mismatch)
    resp = TestClient(signed_in.app).post("/cyl/experiments/3/scans/44/video")
    assert resp.status_code == 409


def test_cyl_video_route_503s_without_naming_the_database(monkeypatch, signed_in):
    def down(client, queue, params):
        raise APIError(
            {"code": "42501", "message": "permission denied for bloom_workflows"}
        )

    _cyl_stubs(monkeypatch, enqueue=down)
    resp = TestClient(signed_in.app).post("/cyl/experiments/3/scans/44/video")
    assert resp.status_code == 503
    assert "bloom_workflows" not in resp.text


@pytest.mark.parametrize(
    "path,body",
    [
        ("/gravi/experiments/12/plate-video", {"plate_id": "P7"}),
        ("/cyl/experiments/3/scans/44/video", None),
    ],
)
def test_video_routes_401_before_any_work(monkeypatch, path, body):
    import main
    import plate_request
    from auth import require_supabase_user

    def _raise_401():
        raise HTTPException(status_code=401, detail="missing token")

    reached = []
    monkeypatch.setattr(plate_request, "enqueue", lambda *a: reached.append(a))
    monkeypatch.setattr(main, "enqueue", lambda *a: reached.append(a))
    main.app.dependency_overrides[require_supabase_user] = _raise_401
    try:
        resp = TestClient(main.app).post(path, json=body)
    finally:
        main.app.dependency_overrides.clear()
    assert resp.status_code == 401
    assert reached == [], "a job was queued for an unauthenticated request"


@pytest.mark.parametrize(
    "path,body",
    [
        ("/gravi/experiments/12/plate-video", {"plate_id": "P7"}),
        ("/cyl/experiments/3/scans/44/video", None),
    ],
)
def test_video_routes_429_before_any_work(monkeypatch, path, body):
    import main
    import plate_request
    from auth import require_supabase_user

    def _rate_limited(user_id):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    reached = []
    monkeypatch.setattr(plate_request, "enqueue", lambda *a: reached.append(a))
    monkeypatch.setattr(main, "enqueue", lambda *a: reached.append(a))
    monkeypatch.setattr(main, "enforce_rate_limit", _rate_limited)
    main.app.dependency_overrides[require_supabase_user] = lambda: "user-1"
    try:
        resp = TestClient(main.app).post(path, json=body)
    finally:
        main.app.dependency_overrides.clear()
    assert resp.status_code == 429
    assert reached == [], "a job was queued for a rate-limited request"


# --- GET /model-cards (bloom#971 phase 1) ------------------------------------

CARDS = [
    {
        "root_type": "lateral",
        "registry_id": "org/wandb-registry-sleap-roots-models/arabidopsis-lateral",
        "version": "v0",
        "selectors": [
            {"species": "arabidopsis", "mode": "cylinder", "age_min": 2, "age_max": 14}
        ],
    }
]
FETCHED_AT = "2026-10-02T12:00:00+00:00"


def test_model_cards_returns_cards_and_fetched_at(monkeypatch):
    import main
    import model_cards
    from auth import require_supabase_user

    main.app.dependency_overrides[require_supabase_user] = lambda: "user-1"
    monkeypatch.setattr(
        model_cards, "list_production_cards", lambda: (CARDS, FETCHED_AT, 0)
    )
    try:
        resp = TestClient(main.app).get("/model-cards")
        assert resp.status_code == 200
        assert resp.json() == {"cards": CARDS, "fetched_at": FETCHED_AT, "skipped": 0}

        monkeypatch.setattr(
            model_cards, "list_production_cards", lambda: ([], FETCHED_AT, 2)
        )
        resp = TestClient(main.app).get("/model-cards")
        assert resp.json() == {"cards": [], "fetched_at": FETCHED_AT, "skipped": 2}
    finally:
        main.app.dependency_overrides.clear()


def test_model_cards_never_spends_the_rate_limit(monkeypatch):
    import auth
    import main
    import model_cards
    from auth import require_supabase_user

    main.app.dependency_overrides[require_supabase_user] = lambda: "user-1"
    monkeypatch.setattr(
        model_cards, "list_production_cards", lambda: (CARDS, FETCHED_AT, 0)
    )
    try:
        client = TestClient(main.app)
        statuses = [client.get("/model-cards").status_code for _ in range(6)]
        assert statuses == [200] * 6
        assert auth._hits.get("user-1", []) == []

        # Its own scope only: the shared count (key "user-1") is untouched.
        assert len(auth._hits.get("model-cards:user-1", [])) == 6
    finally:
        main.app.dependency_overrides.clear()


def test_model_cards_errors_are_fixed_503s(monkeypatch, caplog):
    import main
    import model_cards
    from auth import require_supabase_user

    def raising(exc):
        def _list():
            raise exc

        return _list

    main.app.dependency_overrides[require_supabase_user] = lambda: "user-1"
    try:
        client = TestClient(main.app)

        monkeypatch.setattr(
            model_cards,
            "list_production_cards",
            raising(model_cards.ModelCatalogNotConfigured("no key")),
        )
        resp = client.get("/model-cards")
        assert resp.status_code == 503
        assert resp.json() == {
            "detail": "The model catalog isn't configured in this environment."
        }

        for exc in (
            model_cards.ModelCatalogUnavailable("secret-detail"),
            RuntimeError("secret-detail"),
        ):
            monkeypatch.setattr(model_cards, "list_production_cards", raising(exc))
            caplog.clear()
            resp = client.get("/model-cards")
            assert resp.status_code == 503
            assert resp.json() == {"detail": "Couldn't read the model catalog."}
            assert "secret-detail" not in resp.text
        # The unexpected error's cause reaches the log (the catalog's own
        # failures are logged by model_cards itself).
        assert "secret-detail" in caplog.text
    finally:
        main.app.dependency_overrides.clear()


def test_model_cards_requires_auth(monkeypatch):
    import auth
    import main
    import model_cards
    from auth import require_supabase_user

    called = {"n": 0}

    def _list():
        called["n"] += 1
        return CARDS, FETCHED_AT, 0

    monkeypatch.setattr(model_cards, "list_production_cards", _list)

    def _raise_401():
        raise HTTPException(status_code=401, detail="missing token")

    main.app.dependency_overrides[require_supabase_user] = _raise_401
    try:
        assert TestClient(main.app).get("/model-cards").status_code == 401
    finally:
        main.app.dependency_overrides.clear()

    monkeypatch.setattr(auth, "SUPABASE_URL", "http://kong:8000")
    monkeypatch.setattr(auth, "SUPABASE_ANON_KEY", "anon")
    assert TestClient(main.app).get("/model-cards").status_code == 401
    assert called["n"] == 0


def test_model_cards_route_is_synchronous():
    import inspect

    import main

    assert not inspect.iscoroutinefunction(main.model_cards_route)


def test_startup_warms_the_model_card_cache_without_blocking(monkeypatch):
    import threading

    import main
    import model_cards

    started = threading.Event()
    release = threading.Event()

    def warm():
        started.set()
        release.wait()  # blocks until the test releases it

    monkeypatch.setattr(model_cards, "warm", warm)
    try:
        entered = time.monotonic()
        with TestClient(main.app) as client:
            assert time.monotonic() - entered < 1, "startup waited for the warm-up"
            assert started.wait(2)
            assert client.get("/health").status_code == 200
    finally:
        release.set()


def test_model_cards_has_its_own_rate_limit(monkeypatch):
    import auth
    import main
    import model_cards
    from auth import require_supabase_user

    assert auth.MODEL_CARDS_RATE_LIMIT == 60
    calls = {"n": 0}

    def _list():
        calls["n"] += 1
        return CARDS, FETCHED_AT, 0

    main.app.dependency_overrides[require_supabase_user] = lambda: "user-1"
    monkeypatch.setattr(model_cards, "list_production_cards", _list)
    try:
        client = TestClient(main.app)
        statuses = [client.get("/model-cards").status_code for _ in range(61)]
        assert statuses[:60] == [200] * 60
        assert statuses[60] == 429
        assert calls["n"] == 60
        assert auth._hits.get("user-1", []) == []
    finally:
        main.app.dependency_overrides.clear()
