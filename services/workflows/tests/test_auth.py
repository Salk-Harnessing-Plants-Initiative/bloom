"""Unit tests for caller authentication + rate limiting (imports only auth.py,
so it does not pull the supabase client)."""

import pytest
from fastapi import HTTPException

import auth


class _FakeResp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeClient:
    """Stand-in for httpx.Client used as a context manager."""

    def __init__(self, resp):
        self._resp = resp

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, *a, **k):
        return self._resp


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(auth, "SUPABASE_URL", "http://kong:8000")
    monkeypatch.setattr(auth, "SUPABASE_ANON_KEY", "anon")
    # reset the in-process rate-limit state between tests
    auth._hits.clear()
    monkeypatch.setattr(auth, "_last_sweep", 0.0)


def _patch_user_endpoint(monkeypatch, resp):
    monkeypatch.setattr(auth.httpx, "Client", lambda *a, **k: _FakeClient(resp))


def test_missing_authorization_is_401(monkeypatch):
    with pytest.raises(HTTPException) as e:
        auth.require_supabase_user(authorization=None)
    assert e.value.status_code == 401


def test_non_bearer_is_401():
    with pytest.raises(HTTPException) as e:
        auth.require_supabase_user(authorization="Basic abc")
    assert e.value.status_code == 401


def test_unconfigured_is_500(monkeypatch):
    monkeypatch.setattr(auth, "SUPABASE_URL", None)
    with pytest.raises(HTTPException) as e:
        auth.require_supabase_user(authorization="Bearer x")
    assert e.value.status_code == 500


def test_valid_token_returns_user_id(monkeypatch):
    _patch_user_endpoint(monkeypatch, _FakeResp(200, {"id": "user-123"}))
    assert auth.require_supabase_user(authorization="Bearer good") == "user-123"


def test_supabase_rejects_token_is_401(monkeypatch):
    _patch_user_endpoint(monkeypatch, _FakeResp(401))
    with pytest.raises(HTTPException) as e:
        auth.require_supabase_user(authorization="Bearer bad")
    assert e.value.status_code == 401


def test_rate_limit_allows_then_blocks(monkeypatch):
    monkeypatch.setattr(auth, "RATE_LIMIT", 3)
    for _ in range(3):
        auth.enforce_rate_limit("u1")  # within limit
    with pytest.raises(HTTPException) as e:
        auth.enforce_rate_limit("u1")  # 4th exceeds
    assert e.value.status_code == 429


def test_rate_limit_is_per_user(monkeypatch):
    monkeypatch.setattr(auth, "RATE_LIMIT", 1)
    auth.enforce_rate_limit("a")
    auth.enforce_rate_limit("b")  # different user, own budget — no raise


def test_stale_users_are_evicted(monkeypatch):
    # "old" called once, then went quiet; a later call from "new" past the
    # window triggers the sweep and drops "old" so _hits stays bounded.
    clock = {"t": 1000.0}
    monkeypatch.setattr(auth.time, "time", lambda: clock["t"])
    monkeypatch.setattr(auth, "RATE_WINDOW_SECONDS", 60)

    auth.enforce_rate_limit("old")
    assert "old" in auth._hits

    clock["t"] += 120  # two windows later
    auth.enforce_rate_limit("new")

    assert "old" not in auth._hits  # evicted
    assert "new" in auth._hits


def test_active_users_are_not_evicted(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(auth.time, "time", lambda: clock["t"])
    monkeypatch.setattr(auth, "RATE_WINDOW_SECONDS", 60)

    auth.enforce_rate_limit("u")
    clock["t"] += 90  # a window later, sweep runs...
    auth.enforce_rate_limit("u")  # ...but u is calling now, so it stays
    assert "u" in auth._hits
