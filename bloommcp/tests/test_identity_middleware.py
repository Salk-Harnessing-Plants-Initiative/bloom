"""`bloom_mcp.identity.IdentityMiddleware` — wiring + cross-surface coverage.

Records usage itself, at request granularity (see identity.py's module
docstring for why — a per-tool-call ContextVar design was tried and reverted
after confirming it cannot reach FastMCP's persistent per-session
tool-dispatch task for a reused `streamable-http` session).

Two layers of test:

1. Unit-level, against `IdentityMiddleware` wrapping a bare dummy ASGI app —
   confirms accept/reject behavior, that usage recording is attempted with
   the right (identity, action) pair (or skipped for `/health`), that
   duplicate `X-Bloom-Identity` headers are rejected, and that recording is
   gated on the *wrapped app's own response*, not fired unconditionally
   before delegating to it: a downstream `401` (e.g. FastMCP's own
   `BLOOMMCP_API_KEY` bearer check rejecting the request) suppresses
   recording, since otherwise any caller — with no valid API key and no
   identity header — could generate a `bloommcp_usage` row for every hit,
   reachable today regardless of whether a real client sends the header
   yet. Uses `TestClient` *without* the context-manager form, so no ASGI
   lifespan is driven — the dummy app has none to run.
   `bloom_mcp.usage.record_usage_async` is monkeypatched to a synchronous
   recorder so these tests don't depend on background-thread timing (that's
   `test_usage.py`'s job).
2. Integration-level, against the real `server.build_app()` output via
   `TestClient` entered as a context manager (so FastMCP's own lifespan runs
   and its streamable-http session manager initializes — required for its
   mounted apps to handle a request at all). Confirms the middleware is wired
   in and applies uniformly across the combined surface and every mounted
   section, with no per-section wiring, and that it doesn't short-circuit
   FastMCP's own independent `BLOOMMCP_API_KEY` bearer check (a live
   subprocess test — `bloom_mcp.auth.auth_provider` is built once at
   `bloom_mcp.auth`'s first import, and every other test in this session has
   already imported `bloom_mcp.server` without the key set, so a live
   in-process test can't retroactively exercise the keyed path; mirrors
   `test_devendor_invariants.py::test_server_boots_after_devendor`'s
   subprocess pattern).
"""

from __future__ import annotations

import time
import uuid

import jwt
import pytest
from starlette.testclient import TestClient

from bloom_mcp.identity import IdentityMiddleware, _action_from_path

SECRET = "test-jwt-secret"
A_UUID = str(uuid.uuid4())


def _token(sub=A_UUID, secret=SECRET):
    # verify_identity_header now requires an exp claim to exist
    # (options={"require": ["exp"]}) — every token here must carry one.
    payload = {"sub": sub, "aud": "authenticated", "exp": int(time.time()) + 3600}
    return jwt.encode(payload, secret, algorithm="HS256")


class _DummyApp:
    """A minimal ASGI app that responds with a configurable status and
    records that it was called.

    `user`, when given, is written to `scope["user"]` before responding —
    simulating what Starlette's real `AuthenticationMiddleware` would already
    have done, deeper in the real stack, by the time `IdentityMiddleware`'s
    own `await self.app(...)` returns (see identity.py's
    `_oauth_subject_from_scope` and openspec
    add-bloommcp-oauth-usage-attribution design.md Decision 1)."""

    def __init__(self, status: int = 200, user=None):
        self.called = 0
        self._status = status
        self._user = user

    async def __call__(self, scope, receive, send):
        self.called += 1
        if self._user is not None:
            scope["user"] = self._user
        await send(
            {"type": "http.response.start", "status": self._status, "headers": []}
        )
        await send({"type": "http.response.body", "body": b"ok"})


def _authenticated_user(subject=None):
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
    from mcp.server.auth.provider import AccessToken

    token = AccessToken(token="t", client_id="c", scopes=[], subject=subject)
    return AuthenticatedUser(token)


@pytest.fixture
def recorded_usage(monkeypatch):
    """Synchronously records (identity, action) pairs `IdentityMiddleware`
    attempts to log, bypassing the real background-thread executor so these
    tests aren't racing it."""
    import bloom_mcp.usage as usage

    calls = []
    monkeypatch.setattr(
        usage,
        "record_usage_async",
        lambda identity, action: calls.append((identity, action)),
    )
    return calls


# ── Unit-level: accept/reject + usage-recording behavior ────────────────────
# No `with` block: the dummy app implements no lifespan handler, and doesn't
# need one — the middleware passes non-"http" scopes straight through.


def test_absent_header_is_recorded_as_anonymous(monkeypatch, recorded_usage):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    downstream = _DummyApp()
    client = TestClient(IdentityMiddleware(downstream))
    resp = client.get("/anything")
    assert resp.status_code == 200
    assert downstream.called == 1
    assert recorded_usage == [("anonymous", "combined")]


def test_valid_header_is_recorded_with_resolved_identity(monkeypatch, recorded_usage):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    downstream = _DummyApp()
    client = TestClient(IdentityMiddleware(downstream))
    resp = client.get("/anything", headers={"X-Bloom-Identity": _token()})
    assert resp.status_code == 200
    assert recorded_usage == [(A_UUID, "combined")]


def test_downstream_401_is_not_recorded(monkeypatch, recorded_usage):
    """A valid identity header does not force recording if the *wrapped app*
    itself rejects the request with 401 — e.g. FastMCP's own independent
    `BLOOMMCP_API_KEY` bearer check, which runs downstream of this
    middleware (Decision 3). Without this gate, an unauthenticated caller
    with no valid API key and no `X-Bloom-Identity` header could still
    generate a recording for every request, since this middleware is wired
    in regardless of whether any real client sends the header yet."""
    monkeypatch.setenv("JWT_SECRET", SECRET)
    downstream = _DummyApp(status=401)
    client = TestClient(IdentityMiddleware(downstream))
    resp = client.get("/anything", headers={"X-Bloom-Identity": _token()})
    assert resp.status_code == 401
    assert downstream.called == 1  # our middleware did delegate to it
    assert recorded_usage == []  # but its 401 suppressed recording


def test_downstream_non_401_rejection_is_still_recorded(monkeypatch, recorded_usage):
    """Only a 401 suppresses recording — any other downstream status (e.g. a
    4xx/5xx from the tool/protocol layer itself, unrelated to auth) does not,
    since that's not evidence the caller was unauthenticated."""
    monkeypatch.setenv("JWT_SECRET", SECRET)
    downstream = _DummyApp(status=500)
    client = TestClient(IdentityMiddleware(downstream))
    resp = client.get("/anything", headers={"X-Bloom-Identity": _token()})
    assert resp.status_code == 500
    assert recorded_usage == [(A_UUID, "combined")]


def test_invalid_header_rejects_before_downstream_runs(monkeypatch, recorded_usage):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    downstream = _DummyApp()
    client = TestClient(IdentityMiddleware(downstream))
    resp = client.get("/anything", headers={"X-Bloom-Identity": "not-a-jwt"})
    assert resp.status_code == 401
    assert downstream.called == 0
    assert recorded_usage == []


def test_missing_jwt_secret_with_header_present_returns_500(
    monkeypatch, recorded_usage
):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    downstream = _DummyApp()
    client = TestClient(IdentityMiddleware(downstream))
    resp = client.get("/anything", headers={"X-Bloom-Identity": _token()})
    assert resp.status_code == 500
    assert downstream.called == 0
    assert recorded_usage == []


def test_duplicate_identity_headers_are_rejected(monkeypatch, recorded_usage):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    downstream = _DummyApp()
    client = TestClient(IdentityMiddleware(downstream))
    resp = client.get(
        "/anything",
        headers=[("X-Bloom-Identity", _token()), ("X-Bloom-Identity", _token())],
    )
    assert resp.status_code == 401
    assert downstream.called == 0
    assert recorded_usage == []


def test_health_path_is_not_recorded(monkeypatch, recorded_usage):
    """Even with a valid identity header, `/health` is never recorded — a
    Docker healthcheck must not accumulate usage rows."""
    monkeypatch.setenv("JWT_SECRET", SECRET)
    downstream = _DummyApp()
    client = TestClient(IdentityMiddleware(downstream))
    resp = client.get("/health", headers={"X-Bloom-Identity": _token()})
    assert resp.status_code == 200
    assert downstream.called == 1
    assert recorded_usage == []


# ── OAuth AccessToken fallback (add-bloommcp-oauth-usage-attribution) ───────
# A second identity source, consulted only when no X-Bloom-Identity header
# resolved one. `_DummyApp(user=...)` simulates what Starlette's real
# `AuthenticationMiddleware` writes into `scope["user"]`, deeper in the real
# stack, without needing FastMCP/OAuth actually wired up (see
# test_identity_middleware_records_a_real_oauth_callers_subject_live below for
# the real end-to-end version).


def test_oauth_authenticated_caller_with_no_header_is_recorded_under_their_subject(
    monkeypatch, recorded_usage
):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    downstream = _DummyApp(user=_authenticated_user(subject=A_UUID))
    client = TestClient(IdentityMiddleware(downstream))
    resp = client.get("/anything")
    assert resp.status_code == 200
    assert recorded_usage == [(A_UUID, "combined")]


def test_api_key_authenticated_caller_with_no_header_is_still_anonymous(
    monkeypatch, recorded_usage
):
    """`ApiKeyVerifier`'s shared-key credential never sets `subject` — it
    names no individual, so this must still collapse into `anonymous`."""
    monkeypatch.setenv("JWT_SECRET", SECRET)
    downstream = _DummyApp(user=_authenticated_user(subject=None))
    client = TestClient(IdentityMiddleware(downstream))
    resp = client.get("/anything")
    assert resp.status_code == 200
    assert recorded_usage == [("anonymous", "combined")]


def test_no_auth_configured_with_no_header_is_still_anonymous(
    monkeypatch, recorded_usage
):
    """Dev mode — no `auth` provider at all, so `scope` never gains a `user`
    key. Must not raise, must fall through to anonymous exactly as today."""
    monkeypatch.setenv("JWT_SECRET", SECRET)
    downstream = _DummyApp()  # no user= — no scope["user"] set at all
    client = TestClient(IdentityMiddleware(downstream))
    resp = client.get("/anything")
    assert resp.status_code == 200
    assert recorded_usage == [("anonymous", "combined")]


def test_identity_header_takes_precedence_over_a_simultaneous_oauth_subject(
    monkeypatch, recorded_usage
):
    """If both somehow resolve on one request, the header wins — see
    design.md Decision 2."""
    monkeypatch.setenv("JWT_SECRET", SECRET)
    other_uuid = "22222222-2222-2222-2222-222222222222"
    downstream = _DummyApp(user=_authenticated_user(subject=other_uuid))
    client = TestClient(IdentityMiddleware(downstream))
    resp = client.get("/anything", headers={"X-Bloom-Identity": _token()})
    assert resp.status_code == 200
    assert recorded_usage == [(A_UUID, "combined")]


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/mcp", "combined"),
        ("/health", "combined"),
        ("/core/mcp", "core"),
        ("/sleap_roots/mcp", "sleap_roots"),
        ("/phenotyping_segmentation/mcp", "phenotyping_segmentation"),
        ("/not-a-real-section/mcp", "combined"),
        ("/output/k", "combined"),  # local-mode static mount (#642)
        ("/plots/k", "combined"),  # local-mode static mount (#642)
    ],
)
def test_action_from_path(path, expected):
    assert _action_from_path(path) == expected


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


@pytest.mark.parametrize(
    "path,expected_action",
    [("/mcp", "combined"), ("/core/mcp", "core"), ("/sleap_roots/mcp", "sleap_roots")],
)
def test_real_surface_records_usage_with_correct_action(
    monkeypatch, recorded_usage, path, expected_action
):
    """End-to-end wiring check through the real build_app(): a request to
    each real mounted surface is recorded with the action this surface should
    resolve to."""
    monkeypatch.setenv("JWT_SECRET", SECRET)
    from bloom_mcp import server

    with TestClient(server.build_app()) as client:
        client.get(path, headers={"X-Bloom-Identity": _token()})
    assert recorded_usage == [(A_UUID, expected_action)]


# ── Live subprocess: IdentityMiddleware and BLOOMMCP_API_KEY are independent ──

_DUAL_AUTH_SCRIPT = """
import os
os.environ["JWT_SECRET"] = "test-jwt-secret"
os.environ["BLOOMMCP_API_KEY"] = "test-api-key"

import json
import time
import jwt
from starlette.testclient import TestClient
from bloom_mcp import server

valid_identity = jwt.encode(
    {
        "sub": "11111111-1111-1111-1111-111111111111",
        "aud": "authenticated",
        "exp": int(time.time()) + 3600,
    },
    "test-jwt-secret",
    algorithm="HS256",
)

with TestClient(server.build_app()) as client:
    # Valid identity header, no bearer token — FastMCP's own bearer check
    # must still reject this; our middleware has nothing to object to.
    r1 = client.get("/mcp", headers={"X-Bloom-Identity": valid_identity})
    # Invalid identity header, valid bearer token — our middleware must
    # reject this before FastMCP's own check is ever reached.
    r2 = client.get(
        "/mcp",
        headers={"X-Bloom-Identity": "garbage", "Authorization": "Bearer test-api-key"},
    )

print(json.dumps({"r1": r1.status_code, "r2": r2.status_code}))
"""


def test_identity_middleware_and_bearer_auth_are_independent_live():
    """`bloom_mcp.auth.auth_provider` is built once at import time from
    whatever `BLOOMMCP_API_KEY` is set then — every other test in this
    session already imported `bloom_mcp.server` with the key unset, so this
    can only be exercised in a fresh interpreter (subprocess), not via
    `monkeypatch.setenv` here. Mirrors
    `test_devendor_invariants.py::test_server_boots_after_devendor`'s pattern."""
    import json
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", _DUAL_AUTH_SCRIPT],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    out = json.loads(result.stdout.strip().splitlines()[-1])
    # Rejected by FastMCP's own bearer check (our middleware had no reason to
    # object to a valid identity header) — not necessarily 401 specifically,
    # just definitely not a 2xx success.
    assert not (200 <= out["r1"] < 300), out
    # Rejected by our middleware, before FastMCP's bearer check is reached.
    assert out["r2"] == 401, out


# ── Live subprocess: a real OAuth-authenticated caller is recorded ──────────

_OAUTH_USAGE_SCRIPT = """
import os
os.environ["JWT_SECRET"] = "test-jwt-secret"
os.environ["BLOOMMCP_PUBLIC_URL"] = "http://bloommcp.test"
os.environ["BLOOMMCP_OAUTH_AUTHORIZATION_SERVER"] = "http://auth.test"

import json
import time
import uuid

import jwt
from starlette.testclient import TestClient

import bloom_mcp.usage as usage

recorded = []
usage.record_usage_async = lambda identity, action: recorded.append((identity, action))

from bloom_mcp import server

sub = "33333333-3333-3333-3333-333333333333"
oauth_token = jwt.encode(
    {
        "sub": sub,
        "aud": "authenticated",
        "client_id": str(uuid.uuid4()),
        "exp": int(time.time()) + 3600,
    },
    "test-jwt-secret",
    algorithm="HS256",
)

with TestClient(server.build_app()) as client:
    client.get("/mcp", headers={"Authorization": f"Bearer {oauth_token}"})

print(json.dumps({"recorded": recorded, "sub": sub}))
"""


def test_identity_middleware_records_a_real_oauth_callers_subject_live():
    """End-to-end proof, not a simulation: `bloom_mcp.auth.auth_provider`
    (and its module-level `PUBLIC_URL`/`AUTHORIZATION_SERVER`) are built once
    at import time from whatever OAuth env is set then — every other test in
    this session already imported `bloom_mcp.server` with OAuth unconfigured,
    so this needs a fresh interpreter, mirroring
    `test_identity_middleware_and_bearer_auth_are_independent_live` above. No
    `X-Bloom-Identity` header is sent — only a real Supabase-shaped OAuth
    bearer token — and the request drives the actual
    `AuthenticationMiddleware` -> `BearerAuthBackend` -> `SupabaseOAuthVerifier`
    -> `scope["user"]` -> `IdentityMiddleware` chain for real, through
    `server.build_app()`."""
    import json
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", _OAUTH_USAGE_SCRIPT],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["recorded"] == [[out["sub"], "combined"]], out
