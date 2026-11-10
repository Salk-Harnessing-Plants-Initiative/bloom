"""The Salk connector's tunnel path through `IdentityMiddleware`.

Caddy's tunnel entrance sets `X-Bloom-Tunnel` to `BLOOMMCP_TUNNEL_SECRET` and
passes on AIRC's bearer JWT. With a matching secret, bloommcp verifies the token
against AIRC's keys (here a test key, served from a `file://` JWKS) and maps its
`email` claim to a Bloom account through `bloom_user_for_email`, or refuses.
Without the secret, the request is handled exactly as before.

Tests here wrap a dummy ASGI app; test_tunnel_startup.py covers startup
validation and the real app.
"""

from __future__ import annotations

import json
import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from starlette.testclient import TestClient

from bloom_mcp import auth, signup_email, supabase_client, tunnel
from bloom_mcp.identity import IdentityMiddleware

SECRET = "s" * 40
API_KEY = "test-api-key"
SITE_URL = "https://bloom.example.org"
USER_ID = str(uuid.uuid4())
EMAIL = "jdoe@salk.edu"
ISSUER = "bloom-mcp"
AUDIENCE = "bloom-test"
KID = "test-key"
_KEY = ec.generate_private_key(ec.SECP256R1())


def _token(
    email: str | None = EMAIL, *, key=_KEY, algorithm="ES256", kid=KID, **claims
) -> str:
    """A JWT shaped like AIRC's: ES256, iss/aud/exp, and the user in `email` (and
    an unrelated `sub`, so reading `sub` instead would be caught)."""
    payload = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": int(time.time()) + 300,
        "sub": "entra-object-id",
    }
    if email is not None:
        payload["email"] = email
    payload.update(claims)
    payload = {k: v for k, v in payload.items() if v is not None}
    return jwt.encode(payload, key, algorithm=algorithm, headers={"kid": kid})


_PASSED_THROUGH_TOKEN = _token()


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


def _clear_key_caches():
    tunnel.clear_cache()
    tunnel._jwks_client.cache_clear()
    tunnel._jwks_from_file.cache_clear()


@pytest.fixture(autouse=True)
def _tunnel_env(monkeypatch, tmp_path):
    jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(_KEY.public_key()))
    jwks = tmp_path / "jwks.json"
    jwks.write_text(
        json.dumps({"keys": [{**jwk, "kid": KID, "alg": "ES256", "use": "sig"}]})
    )
    monkeypatch.setenv("BLOOMMCP_TUNNEL_SECRET", SECRET)
    monkeypatch.setenv("BLOOMMCP_TUNNEL_JWKS_FILE", str(jwks))
    monkeypatch.delenv("BLOOMMCP_TUNNEL_JWKS_URL", raising=False)
    monkeypatch.setenv("BLOOMMCP_TUNNEL_JWT_ISSUER", ISSUER)
    monkeypatch.setenv("BLOOMMCP_TUNNEL_JWT_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("SITE_URL", SITE_URL)
    monkeypatch.setattr(auth, "API_KEY", API_KEY)
    _clear_key_caches()
    yield
    _clear_key_caches()


@pytest.fixture
def lookup(monkeypatch):
    fake = _FakeLookup()
    monkeypatch.setattr(supabase_client, "call_rpc", fake)
    return fake


@pytest.fixture(autouse=True)
def signup_requests(monkeypatch):
    """Autouse so no test starts a real request to the mail service; records the addresses."""
    calls = []
    monkeypatch.setattr(signup_email, "request", calls.append)
    return calls


@pytest.fixture(autouse=True)
def recorded_usage(monkeypatch):
    """Autouse so no test reaches the real recorder, which would call the faked RPC."""
    import bloom_mcp.usage as usage

    calls = []
    monkeypatch.setattr(usage, "record_usage_async", lambda i, a: calls.append((i, a)))
    return calls


def _tunnel_headers(*emails: str, secret: str = SECRET) -> list[tuple[str, str]]:
    """The tunnel secret plus one AIRC bearer token per email."""
    return [
        ("X-Bloom-Tunnel", secret),
        *(("Authorization", f"Bearer {_token(e)}") for e in emails),
    ]


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


def test_control_character_in_the_email_claim_is_refused(lookup):
    app = _RecordingApp()
    resp = _get(IdentityMiddleware(app), _tunnel_headers("jdoe@salk.edu\x00"))
    assert resp.status_code == 403
    assert app.called == 0
    assert lookup.calls == []


# --- The token itself -------------------------------------------------------


def _token_headers(token: str) -> list[tuple[str, str]]:
    return [("X-Bloom-Tunnel", SECRET), ("Authorization", f"Bearer {token}")]


_OTHER_KEY = ec.generate_private_key(ec.SECP256R1())


@pytest.mark.parametrize(
    "token",
    [
        _token(aud="bloom-prod"),
        _token(iss="someone-else"),
        _token(exp=int(time.time()) - 3600),
        _token(exp=int(time.time()) - 60),
        _token(exp=None),
        _token(email=None),
        _token(email=["jdoe@salk.edu"]),
        _token(key=_OTHER_KEY),
        _token(kid="unknown-key"),
        _token(key="a-shared-secret-that-is-long-enough-for-hs256", algorithm="HS256"),
        jwt.encode(
            {
                "iss": ISSUER,
                "aud": AUDIENCE,
                "email": EMAIL,
                "exp": int(time.time()) + 300,
            },
            None,
            algorithm="none",
            headers={"kid": KID},
        ),
        "not-a-jwt",
    ],
    ids=[
        "other-audience",
        "other-issuer",
        "expired",
        "expired-past-the-leeway",
        "no-expiry",
        "no-email",
        "email-not-a-string",
        "signed-by-another-key",
        "unknown-kid",
        "hs256",
        "alg-none",
        "garbage",
    ],
)
def test_a_token_that_does_not_verify_is_refused(lookup, recorded_usage, token):
    app = _RecordingApp()
    resp = _get(IdentityMiddleware(app), _token_headers(token))
    assert resp.status_code == 403
    assert (
        "could not be verified" in resp.json()["error"]
        or "@salk.edu" in resp.json()["error"]
    )
    assert app.called == 0
    assert lookup.calls == []
    assert recorded_usage == []


@pytest.mark.parametrize("authorization", [f"Basic {_token()}", "Bearer ", _token()])
def test_authorization_that_is_not_a_bearer_token_is_refused(lookup, authorization):
    headers = [("X-Bloom-Tunnel", SECRET), ("Authorization", authorization)]
    assert _get(IdentityMiddleware(_RecordingApp()), headers).status_code == 403
    assert lookup.calls == []


def test_x_salk_user_alone_no_longer_admits(lookup):
    headers = [("X-Bloom-Tunnel", SECRET), ("X-Salk-User", EMAIL)]
    assert _get(IdentityMiddleware(_RecordingApp()), headers).status_code == 403
    assert lookup.calls == []


def test_the_token_names_the_user_not_x_salk_user(lookup, recorded_usage):
    headers = [*_tunnel_headers(EMAIL), ("X-Salk-User", "director@salk.edu")]
    assert _get(IdentityMiddleware(_RecordingApp()), headers).status_code == 200
    assert lookup.calls == [("bloom_user_for_email", {"p_email": EMAIL})]


def test_unreachable_keys_url_is_503(monkeypatch, lookup):
    """The production path: AIRC's keys URL, here a closed local port."""
    monkeypatch.delenv("BLOOMMCP_TUNNEL_JWKS_FILE")
    monkeypatch.setenv(
        "BLOOMMCP_TUNNEL_JWKS_URL", "http://127.0.0.1:9/.well-known/jwks.json"
    )
    _clear_key_caches()
    resp = _get(IdentityMiddleware(_RecordingApp()), _tunnel_headers(EMAIL))
    assert resp.status_code == 503
    assert "try again shortly" in resp.json()["error"]
    assert lookup.calls == []


@pytest.fixture
def key_server(monkeypatch):
    """Serves the test JWKS over HTTP, recording each fetch's User-Agent; `serve`
    swaps the key set, as AIRC does when it rotates a key."""
    import http.server
    import threading
    import types

    def key_set(key, kid: str) -> bytes:
        jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(key.public_key()))
        return json.dumps(
            {"keys": [{**jwk, "kid": kid, "alg": "ES256", "use": "sig"}]}
        ).encode()

    agents: list[str] = []
    served = [key_set(_KEY, KID)]

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            agents.append(self.headers.get("User-Agent", ""))
            # Like AIRC's Cloudflare: Python's default agent is refused.
            if agents[-1].startswith("Python-urllib"):
                self.send_response(403)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(served[0])

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.delenv("BLOOMMCP_TUNNEL_JWKS_FILE")
    monkeypatch.setenv(
        "BLOOMMCP_TUNNEL_JWKS_URL", f"http://127.0.0.1:{server.server_port}/jwks.json"
    )
    _clear_key_caches()

    def serve(key, kid: str) -> None:
        served[0] = key_set(key, kid)

    yield types.SimpleNamespace(agents=agents, serve=serve)
    server.shutdown()


def test_the_keys_url_is_fetched_with_bloommcps_own_user_agent(key_server, lookup):
    """AIRC's Cloudflare refuses Python's default User-Agent."""
    resp = _get(IdentityMiddleware(_RecordingApp()), _tunnel_headers(EMAIL))
    assert resp.status_code == 200
    agents = key_server.agents
    assert agents and all(a == tunnel.JWKS_USER_AGENT for a in agents)


def test_the_key_set_is_fetched_once_for_many_requests(key_server, lookup):
    middleware = IdentityMiddleware(_RecordingApp())
    for _ in range(3):
        assert _get(middleware, _tunnel_headers(EMAIL)).status_code == 200
    assert len(key_server.agents) == 1


def test_a_key_airc_removes_is_refused_once_the_key_set_cache_expires(
    monkeypatch, key_server, lookup
):
    """A key seen once must not stay trusted: PyJWT's cache_keys would keep it until restart."""
    import jwt.jwk_set_cache

    middleware = IdentityMiddleware(_RecordingApp())
    assert _get(middleware, _tunnel_headers(EMAIL)).status_code == 200
    key_server.serve(_OTHER_KEY, "rotated-key")
    # Moves the clock on rather than freezing it; this is the global time module.
    real = time.monotonic
    monkeypatch.setattr(
        jwt.jwk_set_cache.time,
        "monotonic",
        lambda: real() + tunnel.JWKS_CACHE_SECONDS + 1,
    )
    resp = _get(middleware, _tunnel_headers(EMAIL))
    assert resp.status_code == 403
    assert tunnel.NOT_VERIFIED_MESSAGE in resp.text


def test_a_lowercase_bearer_scheme_is_accepted(lookup):
    headers = [("X-Bloom-Tunnel", SECRET), ("Authorization", f"bearer {_token()}")]
    assert _get(IdentityMiddleware(_RecordingApp()), headers).status_code == 200


@pytest.mark.parametrize(
    "contents", ["not json", None], ids=["not-json", "missing-file"]
)
def test_unusable_keys_file_is_503(monkeypatch, tmp_path, lookup, contents):
    path = tmp_path / "keys.json"
    if contents is not None:
        path.write_text(contents)
    monkeypatch.setenv("BLOOMMCP_TUNNEL_JWKS_FILE", str(path))
    _clear_key_caches()
    resp = _get(IdentityMiddleware(_RecordingApp()), _tunnel_headers(EMAIL))
    assert resp.status_code == 503
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
            "code": -32003,
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
        b'{"jsonrpc": "2.0", "id": true, "method": "ping"}',
        b'{"jsonrpc": "2.0", "id": 5, "result": {}}',
        b"[" * 200_000,
    ],
    ids=[
        "notification",
        "not-json",
        "batch",
        "empty",
        "bool-id",
        "a-response",
        "deeply-nested",
    ],
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
    # Sent with a JSON-RPC body, so only the method keeps it a plain 403.
    resp = TestClient(IdentityMiddleware(_RecordingApp())).request(
        method, "/mcp", headers=_tunnel_headers("newperson@salk.edu"), content=_rpc(1)
    )
    assert resp.status_code == 403
    assert resp.json() == {"error": NO_ACCOUNT_MESSAGE}


def _raw_post(middleware, chunks: list[bytes]) -> list[dict]:
    """A POST whose body arrives in ``chunks``, through raw ASGI."""
    import anyio

    sent, pending = [], list(chunks)

    async def receive():
        if not pending:
            return {"type": "http.disconnect"}
        chunk = pending.pop(0)
        return {"type": "http.request", "body": chunk, "more_body": bool(pending)}

    async def send(message):
        sent.append(message)

    headers = [
        (k.lower().encode(), v.encode())
        for k, v in _tunnel_headers("newperson@salk.edu")
    ]
    scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": headers}
    anyio.run(middleware, scope, receive, send)
    return sent


def test_a_body_in_several_chunks_still_gets_the_jsonrpc_error(monkeypatch):
    import json

    monkeypatch.setattr(supabase_client, "call_rpc", _FakeLookup(accounts={}))
    body = _rpc(9)
    sent = _raw_post(
        IdentityMiddleware(_RecordingApp()), [body[:10], body[10:30], body[30:]]
    )
    assert sent[0]["status"] == 200
    assert json.loads(sent[1]["body"])["id"] == 9


def test_a_body_that_never_finishes_gets_the_plain_403(monkeypatch):
    import anyio

    monkeypatch.setattr(supabase_client, "call_rpc", _FakeLookup(accounts={}))
    monkeypatch.setattr(tunnel, "REFUSAL_BODY_READ_SECONDS", 0.1)
    middleware = IdentityMiddleware(_RecordingApp())
    sent = []

    async def receive():
        await anyio.sleep(10)

    async def send(message):
        sent.append(message)

    headers = [
        (k.lower().encode(), v.encode())
        for k, v in _tunnel_headers("newperson@salk.edu")
    ]
    scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": headers}

    async def bounded():
        with anyio.fail_after(2):
            await middleware(scope, receive, send)

    anyio.run(bounded)
    assert sent[0]["status"] == 403


def test_admitted_post_reaches_the_app_with_its_body(lookup):
    received = {}

    async def app(scope, receive, send):
        import anyio

        # Bounded, so a middleware that already consumed the body fails instead of hanging.
        with anyio.fail_after(2):
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


def test_lookup_failure_log_is_capped(monkeypatch, caplog):
    monkeypatch.setattr(
        supabase_client, "call_rpc", _FakeLookup(error=RuntimeError("x" * 1000))
    )
    with caplog.at_level("ERROR", logger="bloom_mcp.tunnel"):
        _get(IdentityMiddleware(_RecordingApp()), _tunnel_headers(EMAIL))
    (record,) = [r for r in caplog.records if "tunnel lookup failed" in r.getMessage()]
    assert len(record.args[1]) <= tunnel.MAX_LOGGED_ERROR_CHARS


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


def test_a_second_authorization_header_is_refused(lookup):
    app = _RecordingApp()
    headers = [*_tunnel_headers(EMAIL), ("Authorization", "Bearer something-else")]
    assert _get(IdentityMiddleware(app), headers).status_code == 403
    assert app.called == 0


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
        [("Authorization", f"Bearer {_PASSED_THROUGH_TOKEN}")],
        [
            ("X-Bloom-Tunnel", "w" * 40),
            ("Authorization", f"Bearer {_PASSED_THROUGH_TOKEN}"),
        ],
        [
            ("X-Bloom-Tunnel", SECRET),
            ("X-Bloom-Tunnel", SECRET),
            ("Authorization", f"Bearer {_PASSED_THROUGH_TOKEN}"),
        ],
    ],
    ids=["no-secret", "wrong-secret", "two-secrets"],
)
def test_without_a_matching_secret_the_token_is_not_trusted(
    lookup, recorded_usage, headers
):
    """Handled as before: passed on untouched for FastMCP's own token check."""
    app = _RecordingApp(status=401)
    resp = _get(IdentityMiddleware(app), headers)
    assert resp.status_code == 401
    assert app.called == 1
    assert app.header(b"authorization") == [f"Bearer {_PASSED_THROUGH_TOKEN}".encode()]
    assert lookup.calls == []
    assert recorded_usage == []


def test_secret_unset_disables_the_tunnel_path(monkeypatch, lookup):
    monkeypatch.delenv("BLOOMMCP_TUNNEL_SECRET")
    app = _RecordingApp(status=401)
    resp = _get(IdentityMiddleware(app), _tunnel_headers(EMAIL, secret=""))
    assert resp.status_code == 401
    assert lookup.calls == []


def test_empty_secret_keeps_the_tunnel_off(monkeypatch, lookup):
    """Compose passes an unset secret as "", and "" must not match an empty X-Bloom-Tunnel."""
    monkeypatch.setenv("BLOOMMCP_TUNNEL_SECRET", "")
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


def test_unknown_account_requests_the_signup_email_once(monkeypatch, signup_requests):
    monkeypatch.setattr(supabase_client, "call_rpc", _FakeLookup(accounts={}))
    resp = _get(IdentityMiddleware(_RecordingApp()), _tunnel_headers("NewPerson@salk.edu"))
    assert resp.status_code == 403
    assert signup_requests == ["newperson@salk.edu"]


def test_known_account_requests_no_signup_email(lookup, signup_requests):
    assert _get(IdentityMiddleware(_RecordingApp()), _tunnel_headers(EMAIL)).status_code == 200
    assert signup_requests == []


def test_lookup_failure_requests_no_signup_email(monkeypatch, signup_requests):
    fake = _FakeLookup(error=ConnectionError("database unreachable"))
    monkeypatch.setattr(supabase_client, "call_rpc", fake)
    assert _get(IdentityMiddleware(_RecordingApp()), _tunnel_headers(EMAIL)).status_code == 503
    assert signup_requests == []


def test_a_bad_token_requests_no_signup_email(lookup, signup_requests):
    headers = [("X-Bloom-Tunnel", SECRET), ("Authorization", "Bearer not-a-token")]
    assert _get(IdentityMiddleware(_RecordingApp()), headers).status_code == 403
    assert signup_requests == []
