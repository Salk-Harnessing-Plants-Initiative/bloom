"""Shared fixtures + Supabase-free env for the bloom_mcp unit suite.

The whole point of Tier 0 is that this suite runs with **no live Supabase**:
``SUPABASE_URL`` / ``BLOOM_AGENT_KEY`` are explicitly removed so the lazy
validation in ``bloom_mcp.supabase_client`` is exercised, and the non-secret
data directories that ``bloom_mcp.experiment_utils`` requires at import are
pointed at a throwaway temp dir (mirrors ``tests/unit/test_workflow_scaffolding``
in the repo root).
"""

from __future__ import annotations

import os
import tempfile

# --- Guarantee Supabase is absent before any bloom_mcp import ---
os.environ.pop("SUPABASE_URL", None)
os.environ.pop("BLOOM_AGENT_KEY", None)

# --- Non-secret data dirs experiment_utils reads (validated at startup, not import) ---
_TMP = tempfile.mkdtemp(prefix="bloom_mcp_tests_")
os.environ.setdefault("BLOOM_TRAITS_DIR", _TMP)
os.environ.setdefault("BLOOM_OUTPUT_DIR", _TMP)
os.environ.setdefault("BLOOM_PLOTS_DIR", _TMP)
os.environ.setdefault("BLOOM_PLOTS_URL", "http://localhost/plots")


# --- In-memory Supabase Storage boundary (Tier 2 adapter tests) ---------------
#
# The storage stack funnels every read/write through the six bloom_mcp.supabase_client
# helpers (+ the names re-bound into bloom_mcp.manifest.manifest). This fixture
# fakes that boundary in memory so SupabaseReader / SupabaseResultStore run with
# no live Supabase and no `supabase.create_client` call.

import json  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Optional  # noqa: E402

import pytest  # noqa: E402


class _InMemoryObjectStore:
    """A dict-backed stand-in for the bloommcp-data bucket."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def list_prefix(self, prefix: str) -> list[str]:
        norm = (prefix.rstrip("/") + "/") if prefix else ""
        names: set[str] = set()
        for key in self.objects:
            if key.startswith(norm):
                names.add(key[len(norm) :].split("/", 1)[0])
        return sorted(n for n in names if n)

    def read_json(self, key: str) -> dict:
        if key not in self.objects:
            raise KeyError(f"object not found: {key}")
        return json.loads(self.objects[key].decode("utf-8"))

    def write_json(self, key: str, payload: dict) -> None:
        self.objects[key] = json.dumps(payload, indent=2, sort_keys=True).encode(
            "utf-8"
        )

    def upload_file(self, key: str, local_path) -> None:
        self.objects[key] = Path(local_path).read_bytes()

    def download_file(self, key: str, local_path) -> None:
        if key not in self.objects:
            raise KeyError(f"object not found: {key}")
        p = Path(local_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(self.objects[key])

    def delete_files(
        self, keys: list[str], *, timeout_seconds: float | None = None
    ) -> None:
        del timeout_seconds  # in-memory: no network round-trip to bound
        for key in keys:
            self.objects.pop(key, None)


@pytest.fixture
def fake_supabase_storage(monkeypatch):
    """Patch the Supabase storage boundary with an in-memory object store.

    Returns the store so tests can seed/inspect objects directly.
    """
    import bloom_mcp.manifest.manifest as _manifest
    import bloom_mcp.supabase_client as _sc

    store = _InMemoryObjectStore()
    for name in (
        "list_prefix",
        "read_json",
        "write_json",
        "upload_file",
        "download_file",
        "delete_files",
    ):
        monkeypatch.setattr(_sc, name, getattr(store, name))
    for name in ("list_prefix", "read_json", "write_json"):
        monkeypatch.setattr(_manifest, name, getattr(store, name))

    def _no_network(*_a, **_k):  # pragma: no cover - guard
        raise AssertionError("supabase.create_client called — test hit the network")

    monkeypatch.setattr(_sc.supabase, "create_client", _no_network)
    return store


# --- In-memory RPC boundary (bloommcp_usage / call_rpc) -----------------------
#
# `call_rpc` is bloommcp's one seam for calling a Postgres RPC (e.g.
# `record_bloommcp_usage`, see bloom_mcp.usage). This fixture fakes that
# boundary in memory, mirroring `fake_supabase_storage`'s shape: monkeypatch
# the module-level name directly (not the client `.rpc()` call itself) so
# tests never touch a network or real Postgres.


class _FakeRpc:
    """Records every `call_rpc` call and returns a caller-configured result."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._results: dict[str, list[dict]] = {}

    def set_result(self, function_name: str, rows: list[dict]) -> None:
        self._results[function_name] = rows

    def __call__(self, function_name: str, params: dict) -> list[dict]:
        self.calls.append((function_name, dict(params)))
        return self._results.get(function_name, [])


@pytest.fixture
def fake_bloommcp_rpc(monkeypatch):
    """Patch `bloom_mcp.supabase_client.call_rpc` with an in-memory recorder.

    Returns the fake so tests can inspect `.calls` or seed `.set_result(...)`.
    """
    import bloom_mcp.supabase_client as _sc

    fake = _FakeRpc()
    monkeypatch.setattr(_sc, "call_rpc", fake)
    return fake
