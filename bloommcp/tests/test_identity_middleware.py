"""`bloom_mcp.identity.IdentityMiddleware` — wiring + cross-surface coverage.

Two layers of test:

1. Unit-level, against `IdentityMiddleware` wrapping a bare dummy ASGI app —
   confirms the ContextVar is set/reset correctly and rejection short-circuits
   before the downstream app is ever called. Uses `TestClient` *without* the
   context-manager form, so no ASGI lifespan is driven — the dummy app has
   none to run.
2. Integration-level, against the real `server.build_app()` output via
   `TestClient` entered as a context manager (so FastMCP's own lifespan runs
   and its streamable-http session manager initializes — required for its
   mounted apps to handle a request at all; mirrors the `TestClient(app) as
   c:` convention already used in `langchain/tests/conftest.py`). Confirms
   the middleware is wired in and applies uniformly across the combined
   surface and every mounted section, with no per-section wiring — new test
   infrastructure for this package: no existing bloommcp test drives an HTTP
   request through `build_app()`'s actual ASGI surface (existing tool tests
   use `fastmcp.Client`'s in-memory transport, which bypasses `Mount` routing
   and middleware entirely).
"""

from __future__ import annotations

import uuid

import jwt
import pytest
from starlette.testclient import TestClient

from bloom_mcp.identity import ANONYMOUS, IdentityMiddleware, get_current_identity

SECRET = "test-jwt-secret"
A_UUID = str(uuid.uuid4())


def _token(sub=A_UUID, secret=SECRET):
    return jwt.encode({"sub": sub, "aud": "authenticated"}, secret, algorithm="HS256")


class _SeenIdentityApp:
    """A minimal ASGI app that records the identity visible during its call
    and returns 200 with that identity as the body."""

    def __init__(self):
        self.seen: list[str] = []

    async def __call__(self, scope, receive, send):
        self.seen.append(get_current_identity())
        body = self.seen[-1].encode()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": body})


# ── Unit-level: ContextVar + short-circuit behavior ──────────────────────────
# No `with` block: these dummy apps implement no lifespan handler, and don't
# need one — the middleware passes non-"http" scopes straight through, so a
# lifespan event would otherwise reach the dummy app's http-only logic.


def test_absent_header_calls_downstream_as_anonymous(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    downstream = _SeenIdentityApp()
    client = TestClient(IdentityMiddleware(downstream))
    resp = client.get("/anything")
    assert resp.status_code == 200
    assert downstream.seen == [ANONYMOUS]


def test_valid_header_calls_downstream_with_resolved_identity(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    downstream = _SeenIdentityApp()
    client = TestClient(IdentityMiddleware(downstream))
    resp = client.get("/anything", headers={"X-Bloom-Identity": _token()})
    assert resp.status_code == 200
    assert downstream.seen == [A_UUID]


def test_invalid_header_rejects_before_downstream_runs(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    downstream = _SeenIdentityApp()
    client = TestClient(IdentityMiddleware(downstream))
    resp = client.get("/anything", headers={"X-Bloom-Identity": "not-a-jwt"})
    assert resp.status_code == 401
    assert downstream.seen == []


def test_missing_jwt_secret_with_header_present_returns_500(monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    downstream = _SeenIdentityApp()
    client = TestClient(IdentityMiddleware(downstream))
    resp = client.get("/anything", headers={"X-Bloom-Identity": _token()})
    assert resp.status_code == 500
    assert downstream.seen == []


def test_identity_does_not_leak_between_requests(monkeypatch):
    """Each request gets its own resolved identity — a prior request's
    ContextVar value must not leak into the next."""
    monkeypatch.setenv("JWT_SECRET", SECRET)
    downstream = _SeenIdentityApp()
    client = TestClient(IdentityMiddleware(downstream))
    client.get("/anything", headers={"X-Bloom-Identity": _token()})
    client.get("/anything")
    assert downstream.seen == [A_UUID, ANONYMOUS]


# ── Integration-level: real build_app(), every mounted surface ───────────────
# Entered as a context manager so FastMCP's lifespan runs (required for its
# streamable-http session manager to initialize).


def test_health_endpoint_unaffected_by_absent_header(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    from bloom_mcp import server

    with TestClient(server.build_app()) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.text == "ok"


@pytest.mark.parametrize(
    "path",
    [
        "/mcp",
        "/core/mcp",
        "/sleap_roots/mcp",
        "/phenotyping_segmentation/mcp",
        "/health",
    ],
)
def test_invalid_identity_header_rejected_on_every_mounted_surface(monkeypatch, path):
    """One middleware, added once to build_app()'s single Starlette app,
    covers the combined surface and every per-section mount uniformly — no
    per-section wiring needed."""
    monkeypatch.setenv("JWT_SECRET", SECRET)
    from bloom_mcp import server

    with TestClient(server.build_app()) as client:
        resp = client.get(path, headers={"X-Bloom-Identity": "garbage"})
    assert resp.status_code == 401


@pytest.mark.parametrize("path", ["/mcp", "/core/mcp", "/sleap_roots/mcp"])
def test_absent_header_not_rejected_by_identity_middleware(monkeypatch, path):
    """Without a header, the request is never rejected by IdentityMiddleware
    itself (whatever status FastMCP's own protocol handling returns for a bare
    GET is out of scope here — only "not blocked by us" matters)."""
    monkeypatch.setenv("JWT_SECRET", SECRET)
    from bloom_mcp import server

    with TestClient(server.build_app()) as client:
        resp = client.get(path)
    assert resp.status_code not in (401, 500)
