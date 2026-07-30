"""Non-blocking ``bloommcp_usage`` recording, called by
`bloom_mcp.identity.IdentityMiddleware`.

Records one call to the `bloommcp_usage` table per qualifying HTTP request via
the `record_bloommcp_usage` Postgres RPC, run on a background thread so it
never adds latency to the request/response cycle it's attributed to (the RPC
is a synchronous, blocking network call via `supabase-py` — `call_rpc()` has
no async variant). A recording failure — including a failure to even submit
the work — is caught and logged; it can never affect the response already in
flight, since submission happens fire-and-forget, never awaited inline.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

# A small, dedicated pool: usage recording is an observability side effect,
# not on any request's critical path, so it shouldn't compete with or block
# on whatever pool the ASGI server/anyio itself uses for real work. Not
# explicitly shut down — `concurrent.futures` registers its own atexit hook
# that joins in-flight work at interpreter exit; each `call_rpc()` call is a
# single bounded network round-trip (the underlying `httpx`/`supabase-py`
# client's own timeout), not a long-running task, so this doesn't meaningfully
# delay process shutdown.
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="bloommcp-usage")


def _do_record(identity: str, action: str) -> None:
    # Imported lazily (module attribute access, not `from ... import call_rpc`)
    # so a test's `monkeypatch.setattr(supabase_client, "call_rpc", ...)` is
    # visible here — mirrors the existing lazy-import convention the six
    # storage helpers in supabase_client.py already use for the same reason.
    from bloom_mcp import supabase_client

    try:
        supabase_client.call_rpc(
            "record_bloommcp_usage",
            {"p_identity": identity, "p_action": action},
        )
    except Exception:
        logger.exception(
            "failed to record bloommcp_usage (identity=%r, action=%r)",
            identity,
            action,
        )


def record_usage_async(identity: str, action: str) -> None:
    """Fire-and-forget: submit the recording call to a background thread.

    Never blocks the caller and never raises — a submission failure (e.g. the
    executor rejecting work during interpreter shutdown) is caught and logged
    the same as an RPC failure.
    """
    try:
        _EXECUTOR.submit(_do_record, identity, action)
    except Exception:
        logger.exception(
            "failed to submit bloommcp_usage recording (identity=%r, action=%r)",
            identity,
            action,
        )
