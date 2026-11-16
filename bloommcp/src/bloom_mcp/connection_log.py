"""Connector sign-ins and rejected MCP requests, one line each on stdout (the container log)."""

from __future__ import annotations

import json
import logging
import sys

# Long enough for an MCP SDK error message; a response body is never logged whole.
MAX_REASON_CHARS = 200

connection_log = logging.getLogger("bloom_mcp.connections")
connection_log.setLevel(logging.INFO)
connection_log.propagate = False
if not connection_log.handlers:
    connection_log.addHandler(logging.StreamHandler(sys.stdout))


def signed_in(email: str) -> None:
    connection_log.info("connector sign-in: %s", email)


def rejected(status: int, body: bytes, who: str) -> None:
    connection_log.info("mcp request rejected: %d %s (%s)", status, reason(body), who)


def reason(body: bytes) -> str:
    """The error message from a JSON error body, else the start of the body as text."""
    text = body.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            error = error.get("message")
        if isinstance(error, str):
            text = error
    text = " ".join(text.split())
    return text[:MAX_REASON_CHARS] or "(no body)"
