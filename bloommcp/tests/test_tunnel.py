"""The Salk connector's tunnel path through `IdentityMiddleware`.

Caddy's tunnel entrance sets `X-Bloom-Tunnel` to `BLOOMMCP_TUNNEL_SECRET` and
passes on the person's `X-Salk-User`. With a matching secret, bloommcp maps that
email to a Bloom account through `bloom_user_for_email`, or refuses. Without
it, the request is handled exactly as before and `X-Salk-User` is ignored.

Tests here wrap a dummy ASGI app; test_tunnel_startup.py covers startup
validation and the real app.
"""

from __future__ import annotations

import uuid

import pytest
from starlette.testclient import TestClient

from bloom_mcp import auth, supabase_client, tunnel
from bloom_mcp.identity import IdentityMiddleware

SECRET = "s" * 40
API_KEY = "test-api-key"
SITE_URL = "https://bloom.example.org"
USER_ID = str(uuid.uuid4())
EMAIL = "jdoe@salk.edu"


class _RecordingApp:
    """Responds with a fixed status and keeps the headers it was handed."""

    def __init__(self, status: int = 200):
        self.status = status
        self.headers: list[tuple[bytes, bytes]] | None = None
        self.called = 0

    async def __call__(self, scope, receive, send):
        self.called += 1
        self.headers = list(scope.get("headers") or [])
        await send(
            {"type": "http.response.start", "status": self.status, "headers": []}
        )
        await send({"type": "http.response.body", "body": b"ok"})

    def header(self, name: bytes) -> list[bytes]:
        return [v for k, v in self.headers or [] if k == name]


class _FakeLookup:
    """Stands in for `call_rpc("bloom_user_for_email", ...)`."""

    def __init__(
        self, accounts: dict[str, str] | None = None, error: Exception | None = None
    ):
        self.accounts = accounts if accounts is not None else {EMAIL: USER_ID}
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, function_name: str, params: dict):
        self.calls.append((function_name, params))
        if self.error is not None:
            raise self.error
        return self.accounts.get(params["p_email"])


@pytest.fixture(autouse=True)
def _tunnel_env(monkeypatch):
    monkeypatch.setenv("BLOOMMCP_TUNNEL_SECRET", SECRET)
    monkeypatch.setenv("SITE_URL", SITE_URL)
    monkeypatch.setattr(auth, "API_KEY", API_KEY)
    tunnel.clear_cache()
    yield
    tunnel.clear_cache()


@pytest.fixture
def lookup(monkeypatch):
    fake = _FakeLookup()
    monkeypatch.setattr(supabase_client, "call_rpc", fake)
    return fake


@pytest.fixture(autouse=True)
def recorded_usage(monkeypatch):
    """Autouse so no test reaches the real recorder, which would call the faked RPC."""
    import bloom_mcp.usage as usage

    calls = []
    monkeypatch.setattr(usage, "record_usage_async", lambda i, a: calls.append((i, a)))
    return calls


def _tunnel_headers(*salk_users: str, secret: str = SECRET) -> list[tuple[str, str]]:
    return [("X-Bloom-Tunnel", secret), *(("X-Salk-User", u) for u in salk_users)]


def _get(app, headers, path="/mcp"):
    return TestClient(app).get(path, headers=headers)


# --- Refusals with the right secret ----------------------------------------


@pytest.mark.parametrize(
    "salk_users",
    [
        (),
        (EMAIL, EMAIL),
        ("a@salk.edu,b@salk.edu",),
        (" jdoe@salk.edu",),
        ("jdoe@gmail.com",),
        ("jdoe@salk.edu.evil.org",),
        ("@salk.edu",),
        ("a@b@salk.edu",),
        ("j doe@salk.edu",),
        ("x" * 250 + "@salk.edu",),
    ],
    ids=[
        "missing",
        "twice",
        "listed",
        "leading-space",
        "gmail",
        "suffix",
        "no-local-part",
        "two-at",
        "inner-space",
        "too-long",
    ],
)
def test_bad_salk_user_is_refused_with_403(lookup, recorded_usage, salk_users):
    app = _RecordingApp()
    resp = _get(IdentityMiddleware(app), _tunnel_headers(*salk_users))
    assert resp.status_code == 403
    assert app.called == 0
    assert lookup.calls == []
    assert recorded_usage == []


def test_control_character_in_salk_user_is_refused(lookup):
    """Sent as raw ASGI bytes; an HTTP client would refuse to send it."""
    app = _RecordingApp()
    middleware = IdentityMiddleware(app)
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "path": "/mcp",
        "headers": [
            (b"x-bloom-tunnel", SECRET.encode()),
            (b"x-salk-user", b"jdoe@salk.edu\x00"),
        ],
    }
    import anyio

    anyio.run(middleware, scope, receive, send)
    assert sent[0]["status"] == 403
    assert app.called == 0
    assert lookup.calls == []


def test_unknown_account_is_refused_with_the_create_account_message(
    monkeypatch, recorded_usage
):
    fake = _FakeLookup(accounts={})
    monkeypatch.setattr(supabase_client, "call_rpc", fake)
    app = _RecordingApp()
    resp = _get(IdentityMiddleware(app), _tunnel_headers("NewPerson@salk.edu"))
    assert resp.status_code == 403
    assert resp.json() == {
        "error": f"No Bloom account for newperson@salk.edu. Create one at {SITE_URL}, or reach out to the Bloom team."
    }
    assert app.called == 0
    assert recorded_usage == []


def test_create_account_message_without_site_url(monkeypatch):
    monkeypatch.delenv("SITE_URL")
    monkeypatch.setattr(supabase_client, "call_rpc", _FakeLookup(accounts={}))
    resp = _get(
        IdentityMiddleware(_RecordingApp()), _tunnel_headers("newperson@salk.edu")
    )
    assert resp.status_code == 403
    assert resp.json() == {
        "error": "No Bloom account for newperson@salk.edu. Create one, or reach out to the Bloom team."
    }


# --- Refusals as JSON-RPC errors on POST -------------------------------------
# MCP clients show a JSON-RPC error's message; a bare 403 body is usually dropped.

NO_ACCOUNT_MESSAGE = f"No Bloom account for newperson@salk.edu. Create one at {SITE_URL}, or reach out to the Bloom team."


def _post(app, headers, body, path="/mcp"):
    return TestClient(app).post(path, headers=headers, content=body)


def _rpc(request_id) -> bytes:
    import json

    return json.dumps(
        {"jsonrpc": "2.0", "id": request_id, "method": "initialize", "params": {}}
    ).encode()


@pytest.mark.parametrize("request_id", [7, "abc"])
def test_post_refusal_is_a_jsonrpc_error_with_the_message(monkeypatch, request_id):
    monkeypatch.setattr(supabase_client, "call_rpc", _FakeLookup(accounts={}))
    app = _RecordingApp()
    resp = _post(
        IdentityMiddleware(app), _tunnel_headers("newperson@salk.edu"), _rpc(request_id)
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": tunnel.REFUSAL_CODE,
            "message": NO_ACCOUNT_MESSAGE,
            "data": {"status": 403},
        },
    }
    assert app.called == 0


def test_post_lookup_failure_is_a_jsonrpc_error_with_status_503(monkeypatch):
    monkeypatch.setattr(
        supabase_client, "call_rpc", _FakeLookup(error=RuntimeError("down"))
    )
    resp = _post(IdentityMiddleware(_RecordingApp()), _tunnel_headers(EMAIL), _rpc(1))
    assert resp.status_code == 200
    error = resp.json()["error"]
    assert error["data"] == {"status": 503}
    assert "try again shortly" in error["message"]


@pytest.mark.parametrize(
    "body",
    [
        b'{"jsonrpc": "2.0", "method": "notifications/initialized"}',
        b"not json",
        b'[{"jsonrpc": "2.0", "id": 1, "method": "ping"}]',
        b"",
    ],
    ids=["notification", "not-json", "batch", "empty"],
)
def test_post_without_a_single_request_id_keeps_the_plain_403(monkeypatch, body):
    monkeypatch.setattr(supabase_client, "call_rpc", _FakeLookup(accounts={}))
    resp = _post(
        IdentityMiddleware(_RecordingApp()), _tunnel_headers("newperson@salk.edu"), body
    )
    assert resp.status_code == 403
    assert resp.json() == {"error": NO_ACCOUNT_MESSAGE}


def test_oversized_post_keeps_the_plain_403(monkeypatch):
    monkeypatch.setattr(supabase_client, "call_rpc", _FakeLookup(accounts={}))
    body = _rpc(1)[:-1] + b', "pad": "' + b"x" * tunnel.MAX_REFUSAL_BODY_BYTES + b'"}'
    resp = _post(
        IdentityMiddleware(_RecordingApp()), _tunnel_headers("newperson@salk.edu"), body
    )
    assert resp.status_code == 403


@pytest.mark.parametrize("method", ["GET", "DELETE"])
def test_get_and_delete_refusals_stay_plain_403(monkeypatch, method):
    monkeypatch.setattr(supabase_client, "call_rpc", _FakeLookup(accounts={}))
    resp = TestClient(IdentityMiddleware(_RecordingApp())).request(
        method, "/mcp", headers=_tunnel_headers("newperson@salk.edu")
    )
    assert resp.status_code == 403
    assert resp.json() == {"error": NO_ACCOUNT_MESSAGE}


def test_admitted_post_reaches_the_app_with_its_body(lookup):
    received = {}

    async def app(scope, receive, send):
        message = await receive()
        received["body"] = message.get("body", b"")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    resp = _post(IdentityMiddleware(app), _tunnel_headers(EMAIL), _rpc(1))
    assert resp.status_code == 200
    assert received["body"] == _rpc(1)


def test_unknown_account_is_not_cached(monkeypatch):
    fake = _FakeLookup(accounts={})
    monkeypatch.setattr(supabase_client, "call_rpc", fake)
    middleware = IdentityMiddleware(_RecordingApp())
    _get(middleware, _tunnel_headers(EMAIL))
    fake.accounts[EMAIL] = USER_ID
    resp = _get(middleware, _tunnel_headers(EMAIL))
    assert resp.status_code == 200
    assert len(fake.calls) == 2


def test_lookup_failure_is_503_and_not_cached(monkeypatch, recorded_usage):
    fake = _FakeLookup(error=ConnectionError("database unreachable"))
    monkeypatch.setattr(supabase_client, "call_rpc", fake)
    app = _RecordingApp()
    middleware = IdentityMiddleware(app)
    assert _get(middleware, _tunnel_headers(EMAIL)).status_code == 503

    fake.error = None
    assert _get(middleware, _tunnel_headers(EMAIL)).status_code == 200
    assert len(fake.calls) == 2
    assert app.called == 1


def test_lookup_failure_log_names_postgrests_code_and_message(monkeypatch, caplog):
    """A missing function, a bad key and a lost grant are all APIError; the code tells them apart."""
    from postgrest.exceptions import APIError

    missing = APIError(
        {
            "code": "PGRST202",
            "message": "Could not find the function public.bloom_user_for_email",
        }
    )
    monkeypatch.setattr(supabase_client, "call_rpc", _FakeLookup(error=missing))
    with caplog.at_level("ERROR", logger="bloom_mcp.tunnel"):
        assert (
            _get(
                IdentityMiddleware(_RecordingApp()), _tunnel_headers(EMAIL)
            ).status_code
            == 503
        )
    assert "PGRST202" in caplog.text
    assert "Could not find the function public.bloom_user_for_email" in caplog.text


def test_lookup_failure_log_keeps_other_errors_text(monkeypatch, caplog):
    monkeypatch.setattr(
        supabase_client,
        "call_rpc",
        _FakeLookup(error=ConnectionError("database unreachable")),
    )
    with caplog.at_level("ERROR", logger="bloom_mcp.tunnel"):
        _get(IdentityMiddleware(_RecordingApp()), _tunnel_headers(EMAIL))
    assert "ConnectionError" in caplog.text and "database unreachable" in caplog.text


def test_unexpected_lookup_result_is_503(monkeypatch):
    monkeypatch.setattr(
        supabase_client, "call_rpc", _FakeLookup(accounts={EMAIL: "not-a-uuid"})
    )
    resp = _get(IdentityMiddleware(_RecordingApp()), _tunnel_headers(EMAIL))
    assert resp.status_code == 503


# --- Admission --------------------------------------------------------------


def test_known_account_is_admitted_with_the_api_key(lookup, recorded_usage):
    app = _RecordingApp()
    resp = _get(IdentityMiddleware(app), _tunnel_headers("JDoe@Salk.EDU"))
    assert resp.status_code == 200
    assert app.header(b"authorization") == [f"Bearer {API_KEY}".encode()]
    assert app.header(b"x-bloom-tunnel") == []
    assert lookup.calls == [("bloom_user_for_email", {"p_email": EMAIL})]
    assert recorded_usage == [(USER_ID, "combined")]


def test_callers_own_authorization_is_replaced(lookup):
    app = _RecordingApp()
    headers = [*_tunnel_headers(EMAIL), ("Authorization", "Bearer something-else")]
    _get(IdentityMiddleware(app), headers)
    assert app.header(b"authorization") == [f"Bearer {API_KEY}".encode()]


def test_usage_is_recorded_per_section(lookup, recorded_usage):
    _get(IdentityMiddleware(_RecordingApp()), _tunnel_headers(EMAIL), path="/core/mcp")
    assert recorded_usage == [(USER_ID, "core")]


def test_caller_sent_identity_header_cannot_override_the_tunnel_user(
    lookup, recorded_usage
):
    headers = [*_tunnel_headers(EMAIL), ("X-Bloom-Identity", "garbage")]
    resp = _get(IdentityMiddleware(_RecordingApp()), headers)
    assert resp.status_code == 200
    assert recorded_usage == [(USER_ID, "combined")]


def test_downstream_401_is_not_recorded(lookup, recorded_usage):
    resp = _get(IdentityMiddleware(_RecordingApp(status=401)), _tunnel_headers(EMAIL))
    assert resp.status_code == 401
    assert recorded_usage == []


def test_missing_api_key_refuses_the_tunnel_request(monkeypatch, lookup):
    monkeypatch.setattr(auth, "API_KEY", None)
    app = _RecordingApp()
    resp = _get(IdentityMiddleware(app), _tunnel_headers(EMAIL))
    assert resp.status_code == 503
    assert app.called == 0


# --- Cache ------------------------------------------------------------------


def test_cache_hit_within_ttl_makes_no_second_lookup(monkeypatch, lookup):
    now = [1000.0]
    monkeypatch.setattr(tunnel, "_now", lambda: now[0])
    middleware = IdentityMiddleware(_RecordingApp())
    _get(middleware, _tunnel_headers(EMAIL))
    now[0] += tunnel.CACHE_TTL_SECONDS - 1
    assert _get(middleware, _tunnel_headers("JDOE@salk.edu")).status_code == 200
    assert len(lookup.calls) == 1


def test_cache_expires_after_ttl(monkeypatch, lookup):
    now = [1000.0]
    monkeypatch.setattr(tunnel, "_now", lambda: now[0])
    middleware = IdentityMiddleware(_RecordingApp())
    _get(middleware, _tunnel_headers(EMAIL))
    now[0] += tunnel.CACHE_TTL_SECONDS + 1
    _get(middleware, _tunnel_headers(EMAIL))
    assert len(lookup.calls) == 2


def test_removed_account_is_refused_after_expiry(monkeypatch, lookup):
    now = [1000.0]
    monkeypatch.setattr(tunnel, "_now", lambda: now[0])
    middleware = IdentityMiddleware(_RecordingApp())
    _get(middleware, _tunnel_headers(EMAIL))
    lookup.accounts.clear()
    now[0] += tunnel.CACHE_TTL_SECONDS + 1
    assert _get(middleware, _tunnel_headers(EMAIL)).status_code == 403


def test_cache_is_bounded(monkeypatch):
    accounts = {
        f"u{i}@salk.edu": str(uuid.uuid4()) for i in range(tunnel.CACHE_MAX_ENTRIES + 5)
    }
    monkeypatch.setattr(supabase_client, "call_rpc", _FakeLookup(accounts=accounts))
    middleware = IdentityMiddleware(_RecordingApp())
    for email in accounts:
        _get(middleware, _tunnel_headers(email))
    assert tunnel.cache_size() == tunnel.CACHE_MAX_ENTRIES


# --- Not a tunnel request ---------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        [("X-Salk-User", EMAIL)],
        [("X-Bloom-Tunnel", "w" * 40), ("X-Salk-User", EMAIL)],
        [
            ("X-Bloom-Tunnel", SECRET),
            ("X-Bloom-Tunnel", SECRET),
            ("X-Salk-User", EMAIL),
        ],
    ],
    ids=["no-secret", "wrong-secret", "two-secrets"],
)
def test_without_a_matching_secret_salk_user_is_ignored(
    lookup, recorded_usage, headers
):
    """Handled as before: passed on untouched for FastMCP's own token check."""
    app = _RecordingApp(status=401)
    resp = _get(IdentityMiddleware(app), headers)
    assert resp.status_code == 401
    assert app.called == 1
    assert app.header(b"authorization") == []
    assert lookup.calls == []
    assert recorded_usage == []


def test_secret_unset_disables_the_tunnel_path(monkeypatch, lookup):
    monkeypatch.delenv("BLOOMMCP_TUNNEL_SECRET")
    app = _RecordingApp(status=401)
    resp = _get(IdentityMiddleware(app), _tunnel_headers(EMAIL, secret=""))
    assert resp.status_code == 401
    assert lookup.calls == []


def test_health_needs_neither_header(lookup):
    app = _RecordingApp()
    assert _get(IdentityMiddleware(app), [], path="/health").status_code == 200
    assert lookup.calls == []


def test_health_with_tunnel_secret_is_not_checked(lookup):
    app = _RecordingApp()
    resp = _get(IdentityMiddleware(app), _tunnel_headers(), path="/health")
    assert resp.status_code == 200
    assert lookup.calls == []
