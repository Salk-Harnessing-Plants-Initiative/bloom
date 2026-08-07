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

from bloom_mcp.usage import _redact_identity, record_usage_async


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


# --- Identity redaction in log output (CodeQL: clear-text logging) ---------
# `identity` can be a real Supabase user id sourced from a verified OAuth
# caller (add-bloommcp-oauth-usage-attribution), not only ever the
# X-Bloom-Identity header's sub or the literal "anonymous" — CodeQL flags the
# raw value reaching these log calls as a credential-shaped source landing in
# a clear-text sink. `_redact_identity` breaks that direct flow.


def test_redact_identity_is_deterministic_and_excludes_the_raw_value():
    real_id = "11111111-1111-1111-1111-111111111111"
    redacted = _redact_identity(real_id)
    assert redacted == _redact_identity(real_id)  # deterministic, for correlation
    assert real_id not in redacted
    assert _redact_identity("22222222-2222-2222-2222-222222222222") != redacted


class _RecordingLogger:
    """Stand-in for `usage.logger` that records fully-formatted messages
    (`msg % args`, matching stdlib `logging`'s own deferred-formatting
    convention) and signals `done` when a message is recorded.

    `caplog.at_level(...)` races the background thread `_do_record` runs on:
    `done.set()` inside a `call_rpc` stub can fire, and the main thread's
    `done.wait()` return and exit the `with caplog.at_level(...)` block
    (detaching its capture handler), *before* the background thread's own
    `except` clause has actually reached its `logger.exception(...)` call —
    an earlier version of these two tests hit exactly this race
    intermittently when run after other test files. Recording directly on a
    fake `logger` sidesteps it: there is no capture-handler attach/detach
    window to race against.
    """

    def __init__(self):
        self.messages = []
        self.done = threading.Event()

    def exception(self, msg, *args):
        self.messages.append(msg % args if args else msg)
        self.done.set()

    def warning(self, msg, *args):
        self.messages.append(msg % args if args else msg)
        self.done.set()


def test_dropped_recording_log_never_contains_the_raw_identity(monkeypatch):
    import bloom_mcp.usage as usage

    monkeypatch.setattr(usage, "_inflight", threading.Semaphore(1))
    fake_logger = _RecordingLogger()
    monkeypatch.setattr(usage, "logger", fake_logger)

    release = threading.Event()

    def _blocking_call(function_name, params):
        release.wait(timeout=2)
        return []

    import bloom_mcp.supabase_client as sc

    monkeypatch.setattr(sc, "call_rpc", _blocking_call)

    real_id = "33333333-3333-3333-3333-333333333333"
    try:
        # Occupies the sole in-flight slot; the drop below is logged
        # synchronously, before record_usage_async even returns, so there is
        # no background-thread race for this one specifically — kept on the
        # same fake logger as the test below for a consistent assertion style.
        record_usage_async(real_id, "core")
        record_usage_async(real_id, "core")
        assert any("dropped" in m for m in fake_logger.messages)
        assert not any(real_id in m for m in fake_logger.messages)
    finally:
        release.set()


def test_call_rpc_failure_log_never_contains_the_raw_identity(monkeypatch):
    import bloom_mcp.supabase_client as sc
    import bloom_mcp.usage as usage

    fake_logger = _RecordingLogger()
    monkeypatch.setattr(usage, "logger", fake_logger)

    def _boom(*_a, **_k):
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(sc, "call_rpc", _boom)

    real_id = "44444444-4444-4444-4444-444444444444"
    record_usage_async(real_id, "combined")
    assert fake_logger.done.wait(timeout=2), "background call never logged"

    assert any("failed to record" in m for m in fake_logger.messages)
    assert not any(real_id in m for m in fake_logger.messages)
