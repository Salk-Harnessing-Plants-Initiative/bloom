"""Per-tool usage recording, applied by `contract.wrap.register()`.

Reads the resolved caller identity (`bloom_mcp.identity.get_current_identity`,
set by `IdentityMiddleware` — `"anonymous"` when no `X-Bloom-Identity` header
was present) and records one call to the `bloommcp_usage` table per tool
invocation via the `record_bloommcp_usage` Postgres RPC. A recording failure
is caught and logged; it never fails the underlying tool call — usage
tracking is observability, not a functional gate.

Deliberately separate from `contract.wrap.as_mcp_tool`, which stays exactly
as I/O-free as it is today: this wraps an *already*-`as_mcp_tool`-wrapped
callable one layer out, applied by `register()` at registration time (see
openspec/changes/add-bloommcp-caller-identity/design.md Decision 4).
"""

from __future__ import annotations

import functools
import logging
from typing import Callable

from bloom_mcp.identity import get_current_identity

logger = logging.getLogger(__name__)


def _record_usage(tool_name: str) -> None:
    # Imported lazily (module attribute access, not `from ... import call_rpc`)
    # so a test's `monkeypatch.setattr(supabase_client, "call_rpc", ...)` is
    # visible here — mirrors the existing lazy-import convention the six
    # storage helpers in supabase_client.py already use for the same reason.
    from bloom_mcp import supabase_client

    try:
        supabase_client.call_rpc(
            "record_bloommcp_usage",
            {"p_identity": get_current_identity(), "p_action": tool_name},
        )
    except Exception:
        logger.exception("failed to record bloommcp_usage for tool %r", tool_name)


def with_usage_recording(tool: Callable) -> Callable:
    """Wrap a contract-wrapped tool so each call records usage after it runs.

    Usage is recorded whether the call succeeds or raises — a `finally`
    block, not an `except`, so the original result/exception is always what
    the caller sees; `_record_usage` never itself raises.
    """
    tool_name = tool.__name__

    @functools.wraps(tool)
    def wrapper(*args, **kwargs):
        try:
            return tool(*args, **kwargs)
        finally:
            _record_usage(tool_name)

    return wrapper
