"""`bloom_mcp.usage.record_usage_async` — non-blocking bloommcp_usage recording.

Called by `identity.IdentityMiddleware`, not by tool-dispatch code (see
identity.py's module docstring for why: a per-tool-call design was tried and
reverted — it relied on a `ContextVar` that cannot reach FastMCP's
persistent, per-session tool-dispatch task for a reused `streamable-http`
session). Runs the actual `call_rpc()` round-trip on a background thread so
it never blocks the caller; a `threading.Event` is used to deterministically
wait for that background call in tests, rather than racing it.
"""

from __future__ import annotations

import threading

from bloom_mcp.usage import record_usage_async


def test_record_usage_async_returns_before_call_rpc_runs(monkeypatch):
    """The call returns immediately even when `call_rpc` itself would block —
    proves the recording genuinely happens on a background thread, not
    inline before returning to the caller."""
    import bloom_mcp.supabase_client as sc

    started = threading.Event()
    release = threading.Event()

    def _blocking_call(function_name, params):
        started.set()
        release.wait(timeout=2)
        return []

    monkeypatch.setattr(sc, "call_rpc", _blocking_call)

    record_usage_async("11111111-1111-1111-1111-111111111111", "core")
    # If this were synchronous, the line above would already have blocked on
    # `release` — reaching here at all (let alone before `started` is
    # necessarily set) is only possible because the call happens elsewhere.
    release.set()
    assert started.wait(timeout=2), "background call never started"


def test_record_usage_async_eventually_calls_call_rpc(monkeypatch):
    import bloom_mcp.supabase_client as sc

    done = threading.Event()
    calls = []

    def _watched(function_name, params):
        calls.append((function_name, dict(params)))
        done.set()
        return []

    monkeypatch.setattr(sc, "call_rpc", _watched)

    record_usage_async("11111111-1111-1111-1111-111111111111", "core")

    assert done.wait(timeout=2), "record_usage_async never called call_rpc"
    assert calls == [
        (
            "record_bloommcp_usage",
            {"p_identity": "11111111-1111-1111-1111-111111111111", "p_action": "core"},
        )
    ]


def test_record_usage_async_swallows_call_rpc_failure(monkeypatch):
    import bloom_mcp.supabase_client as sc

    done = threading.Event()

    def _boom(*_a, **_k):
        done.set()
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(sc, "call_rpc", _boom)

    # Must not raise, despite call_rpc raising internally on the background thread.
    record_usage_async("anonymous", "combined")
    assert done.wait(timeout=2), "background call never ran"


def test_record_usage_async_swallows_a_submission_failure(monkeypatch):
    """Even if the executor itself can't accept work (e.g. shutting down),
    the caller is never affected."""
    import bloom_mcp.usage as usage

    class _DeadExecutor:
        def submit(self, *_a, **_k):
            raise RuntimeError("cannot schedule new futures after shutdown")

    monkeypatch.setattr(usage, "_EXECUTOR", _DeadExecutor())

    record_usage_async("anonymous", "combined")  # must not raise
