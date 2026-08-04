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
import time

from bloom_mcp.usage import record_usage_async


def test_record_usage_async_returns_before_call_rpc_runs(monkeypatch):
    """The call returns near-instantly even when `call_rpc` itself blocks for
    a while — an explicit wall-clock bound, not just event ordering, so a
    regression to synchronous execution *fails* this test outright rather
    than merely making it slower without ever failing (the gap an earlier
    draft of this test had, per review)."""
    import bloom_mcp.supabase_client as sc

    release = threading.Event()

    def _blocking_call(function_name, params):
        release.wait(timeout=2)
        return []

    monkeypatch.setattr(sc, "call_rpc", _blocking_call)

    start = time.perf_counter()
    record_usage_async("11111111-1111-1111-1111-111111111111", "core")
    elapsed = time.perf_counter() - start

    release.set()  # let the background thread finish; don't leak it running
    assert elapsed < 0.5, (
        f"record_usage_async took {elapsed:.3f}s to return — expected "
        "near-instant. A regression to synchronous recording would block "
        "for the full duration of the (deliberately slow) call_rpc call "
        "instead."
    )


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


def test_record_usage_async_drops_when_too_many_in_flight(monkeypatch, caplog):
    """`ThreadPoolExecutor`'s own work queue is unbounded — without a cap, a
    burst of traffic would queue arbitrarily many recording calls, all
    eventually serializing on the same hot `bloommcp_usage` row (most
    commonly the `anonymous` sentinel). `_inflight` bounds how many
    recording calls may be outstanding at once; beyond that, calls are
    dropped (logged), never queued or blocked on."""
    import bloom_mcp.supabase_client as sc
    import bloom_mcp.usage as usage

    monkeypatch.setattr(usage, "_inflight", threading.Semaphore(1))

    release = threading.Event()
    calls = []

    def _blocking_call(function_name, params):
        calls.append((function_name, dict(params)))
        release.wait(timeout=2)
        return []

    monkeypatch.setattr(sc, "call_rpc", _blocking_call)

    try:
        # Occupies the sole in-flight slot (the semaphore is acquired
        # synchronously inside record_usage_async, before the background
        # thread even starts, so this is deterministic with no sleep needed).
        record_usage_async("11111111-1111-1111-1111-111111111111", "core")

        with caplog.at_level("WARNING"):
            record_usage_async("22222222-2222-2222-2222-222222222222", "core")
        assert any("dropped" in r.message for r in caplog.records)
    finally:
        release.set()
