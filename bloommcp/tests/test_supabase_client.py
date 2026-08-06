"""`bloom_mcp.supabase_client.call_rpc` — new in this change (openspec
add-bloommcp-caller-identity design.md Decision 8: this function did not
exist on this change's base branch; it is not a reuse of anything).

Follows `get_postgrest_client()`'s existing per-call-fresh-client convention:
construct a client, call `.rpc(function_name, params).execute()`, return the
rows. Runs with no live Supabase (see conftest) — the client itself is
monkeypatched here; `fake_bloommcp_rpc` (conftest.py) monkeypatches
`call_rpc` directly for tests of code that merely *calls* it.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import time
import uuid
from pathlib import Path

import jwt

import bloom_mcp.supabase_client as sc
from bloom_mcp.auth import SupabaseOAuthVerifier

_SRC = Path(__file__).resolve().parents[1] / "src" / "bloom_mcp"


class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeRpcBuilder:
    def __init__(self, recorder, function_name, params):
        self._recorder = recorder
        self._function_name = function_name
        self._params = params

    def execute(self):
        self._recorder.append((self._function_name, self._params))
        return _FakeResponse([{"ok": True}])


class _FakeClient:
    def __init__(self, recorder):
        self._recorder = recorder

    def rpc(self, function_name, params):
        return _FakeRpcBuilder(self._recorder, function_name, params)


def test_call_rpc_delegates_to_postgrest_client_and_returns_rows(monkeypatch):
    calls = []
    monkeypatch.setattr(sc, "get_postgrest_client", lambda: _FakeClient(calls))

    rows = sc.call_rpc("record_bloommcp_usage", {"p_identity": "x", "p_action": "y"})

    assert rows == [{"ok": True}]
    assert calls == [("record_bloommcp_usage", {"p_identity": "x", "p_action": "y"})]


def test_call_rpc_uses_a_fresh_client_per_call(monkeypatch):
    """Matches `get_postgrest_client()`'s own per-call-fresh-client convention
    — no caching of the client across calls."""
    client_build_count = {"n": 0}

    def _build():
        client_build_count["n"] += 1
        return _FakeClient([])

    monkeypatch.setattr(sc, "get_postgrest_client", _build)

    sc.call_rpc("f", {})
    sc.call_rpc("f", {})

    assert client_build_count["n"] == 2


# ─── Caller-token / DB-authority invariant (bloom PR #613 gap) ────────────────
#
# `bloom_mcp.auth` verifies a caller's own credential (API key or Supabase
# OAuth access token) purely for *admission* to bloommcp — see that module's
# docstring. Neither credential is meant to become *database* authority:
# every PostgREST/Storage call must run as the shared `bloom_agent` role
# regardless of who authenticated. PR #613 flagged this as holding "by
# construction" but untested; pinned here so a future change that threads a
# caller's token into a DB call is caught by CI instead of by audit.


def _create_client_call_sites(tree: ast.AST) -> list[ast.Call]:
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "create_client":
            sites.append(node)
        elif isinstance(func, ast.Name) and func.id == "create_client":
            sites.append(node)
    return sites


def test_create_client_is_called_only_from_supabase_client_module():
    """`supabase.create_client` must stay a single choke point.

    If a future change threaded a caller's own token into a second call site
    (e.g. a tool building its own client instead of going through
    `get_postgrest_client`/`get_storage_client`), that client could
    authenticate as whoever the caller happens to be instead of
    `bloom_agent`.
    """
    offenders = []
    for path in _SRC.rglob("*.py"):
        if path.name == "supabase_client.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if _create_client_call_sites(tree):
            offenders.append(str(path.relative_to(_SRC)))
    message = f"create_client called outside supabase_client.py: {offenders}"
    assert not offenders, message

    own_tree = ast.parse((_SRC / "supabase_client.py").read_text(encoding="utf-8"))
    message = "supabase_client.py must itself call create_client — did it move?"
    assert _create_client_call_sites(own_tree), message


def test_client_accessors_accept_no_caller_credential_parameter():
    """Neither client accessor can be handed a per-caller token even by
    accident — their signatures admit no such parameter."""
    assert set(inspect.signature(sc.get_postgrest_client).parameters) == set()
    assert set(inspect.signature(sc.get_storage_client).parameters) == {
        "timeout_seconds"
    }


def test_postgrest_client_ignores_an_authenticated_callers_oauth_token(monkeypatch):
    """Verifying a caller's OAuth token (admission) must have zero effect on
    which credentials the resulting DB client carries (authority).

    Simulates the case PR #613 introduced: a fully verified caller identity —
    a different subject, a different role — sitting in scope right before the
    DB client is built. `get_postgrest_client` takes no argument and reads
    only the server's own env, so the client must come back keyed as
    `bloom_agent` regardless of that caller.
    """
    secret = "test-jwt-secret"
    monkeypatch.setenv("JWT_SECRET", secret)
    monkeypatch.setenv("SUPABASE_URL", "http://x")
    monkeypatch.setenv("BLOOM_AGENT_KEY", "bloom-agent-key")

    caller_token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "role": "bloom_admin",
            "aud": "authenticated",
            "client_id": str(uuid.uuid4()),
            "exp": int(time.time()) + 3600,
        },
        secret,
        algorithm="HS256",
    )
    caller = asyncio.run(SupabaseOAuthVerifier().verify_token(caller_token))
    assert caller is not None  # the caller really is authenticated

    captured = {}

    def _fake_create_client(url, key):
        captured["url"] = url
        captured["key"] = key
        return object()

    monkeypatch.setattr(sc.supabase, "create_client", _fake_create_client)

    sc.get_postgrest_client()

    assert captured["key"] == "bloom-agent-key"
    assert captured["key"] != caller_token
    assert captured["key"] != caller.token
