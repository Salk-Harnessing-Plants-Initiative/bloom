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

import bloom_mcp.supabase_client as sc


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
