"""Non-blocking ``bloommcp_usage`` recording, called by
`bloom_mcp.identity.IdentityMiddleware`.

Records one call to the `bloommcp_usage` table per qualifying HTTP request via
the `record_bloommcp_usage` Postgres RPC, run on a background thread so it
never adds latency to the request/response cycle it's attributed to (the RPC
is a synchronous, blocking network call via `supabase-py` — `call_rpc()` has
no async variant). A recording failure — including a failure to even submit
the work — is caught and logged as a warning (not `logger.exception`, so no
full traceback — bloom#641); it can never affect the response already in
flight, since submission happens fire-and-forget, never awaited inline. Not
called at all for the `local` storage backend — see
`bloom_mcp.identity.IdentityMiddleware`.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


# A fixed, public context string, not a per-value random salt — correlating
# log lines requires the same `identity` to always redact to the same
# output, and (see _redact_identity's docstring) there is no actual secret
# here a random salt would protect. Only exists to make the KDF call below
# depend on more than just `identity` itself.
_REDACTION_CONTEXT = b"bloommcp-usage-log-redaction"


def _redact_identity(identity: str) -> str:
    """A short, non-reversible token for correlating log lines, not the raw
    identity.

    `identity` can now be a real Supabase user id — sourced from a verified
    OAuth caller via `bloom_mcp.identity._oauth_subject_from_scope`
    (add-bloommcp-oauth-usage-attribution) — rather than only ever the
    `X-Bloom-Identity` header's `sub` or the literal `anonymous`. CodeQL
    flags the raw value reaching the log calls below as clear-text logging
    of a credential-shaped source once that path exists.

    Uses PBKDF2-HMAC-SHA256 — a real key-derivation function — rather than a
    bare `hashlib.sha256(...)` call, which trips a *second*, different
    CodeQL rule ("weak password hashing") once the first is fixed: CodeQL
    treats `identity` as password-shaped and flags a fast, general-purpose
    hash used on it. Neither rule is actually about a real weakness here —
    `identity` is a high-entropy UUID or the literal `anonymous`, never
    checked against a stored value for authentication, so it has none of
    the properties (attacker-guessable, low-entropy, used as a credential)
    that make either finding a real risk. A low iteration count is
    deliberate: this line can run synchronously on the request path (the
    in-flight-cap drop in `record_usage_async`), and there is no password
    strength to buy with a higher one.
    """
    return hashlib.pbkdf2_hmac("sha256", identity.encode(), _REDACTION_CONTEXT, 10_000)[
        :6
    ].hex()


# A small, dedicated pool: usage recording is an observability side effect,
# not on any request's critical path, so it shouldn't compete with or block
# on whatever pool the ASGI server/anyio itself uses for real work. Not
# explicitly shut down — `concurrent.futures` registers its own atexit hook
# that joins in-flight work at interpreter exit; each `call_rpc()` call is a
# single bounded network round-trip (the underlying `httpx`/`supabase-py`
# client's own timeout), not a long-running task, so this doesn't meaningfully
# delay process shutdown.
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="bloommcp-usage")

# `ThreadPoolExecutor` itself has an unbounded work queue — under a burst of
# traffic, unbounded submission would queue arbitrarily many recording calls,
# all eventually serializing on the same hot bloommcp_usage row (a single
# identity, most commonly the 'anonymous' sentinel). This semaphore caps how
# many recording calls may be in flight (queued + running) at once;
# `record_usage_async` drops (logs, does not block or raise) rather than
# queue unboundedly once the cap is hit — usage tracking is observability,
# not a delivery guarantee.
_MAX_INFLIGHT = 64
_inflight = threading.Semaphore(_MAX_INFLIGHT)


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
    except Exception as exc:
        # Best-effort, non-blocking recording (module docstring) — a failure
        # here shouldn't look like a crash, so this is a warning naming the
        # error, not a full traceback (bloom#641). Only the exception's type
        # name is interpolated, never `exc`/`str(exc)` itself: today's
        # `record_bloommcp_usage` RPC is a plain upsert that can't echo
        # `p_identity` back, but a future schema constraint or a
        # postgrest-py `APIError` with a `DETAIL` field could carry the raw
        # identity in its message, which would defeat `_redact_identity`
        # right next to it (review follow-up, PR #659).
        logger.warning(
            "failed to record bloommcp_usage (identity=%s, action=%r): %s",
            _redact_identity(identity),
            action,
            type(exc).__name__,
        )
    finally:
        _inflight.release()


def record_usage_async(identity: str, action: str) -> None:
    """Fire-and-forget: submit the recording call to a background thread.

    Never blocks the caller and never raises. Drops (logs) rather than
    queues if `_MAX_INFLIGHT` recording calls are already in flight — see the
    module-level comment on `_inflight`. A submission failure for another
    reason (e.g. the executor rejecting work during interpreter shutdown) is
    also caught and logged, the same as an RPC failure.
    """
    if not _inflight.acquire(blocking=False):
        logger.warning(
            "bloommcp_usage recording dropped (>= %d already in flight): "
            "identity=%s action=%r",
            _MAX_INFLIGHT,
            _redact_identity(identity),
            action,
        )
        return
    try:
        _EXECUTOR.submit(_do_record, identity, action)
    except Exception as exc:
        _inflight.release()
        # Same redaction-bypass reasoning as `_do_record`'s except clause:
        # only the exception's type name is logged, never `exc` itself.
        logger.warning(
            "failed to submit bloommcp_usage recording (identity=%s, action=%r): %s",
            _redact_identity(identity),
            action,
            type(exc).__name__,
        )
