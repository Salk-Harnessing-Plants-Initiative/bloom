"""Connector sign-ins and rejected MCP requests, one line each on stdout (the container log)."""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
import time
from collections import OrderedDict

# Long enough for an MCP SDK error message; a response body is never logged whole.
MAX_REASON_CHARS = 200
# A user is logged as signed in again only after this long without a request.
SIGN_IN_GAP_SECONDS = 3600
# Users whose last request is remembered; the least recent is forgotten first.
MAX_TRACKED_USERS = 1024
# The start of a JSON "message" value, for a body cut before its JSON ends.
_CUT_MESSAGE = re.compile(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)')

connection_log = logging.getLogger("bloom_mcp.connections")
connection_log.setLevel(logging.INFO)
connection_log.propagate = False
if not connection_log.handlers:
    connection_log.addHandler(logging.StreamHandler(sys.stdout))


_now = time.monotonic
_last_seen: OrderedDict[str, float] = OrderedDict()
_last_seen_lock = threading.Lock()


def signed_in(email: str) -> None:
    """Call on every admitted connector request; logs a user's first one after a gap."""
    now = _now()
    with _last_seen_lock:
        last = _last_seen.get(email)
        _last_seen[email] = now
        _last_seen.move_to_end(email)
        while len(_last_seen) > MAX_TRACKED_USERS:
            _last_seen.popitem(last=False)
    if last is None or now - last > SIGN_IN_GAP_SECONDS:
        connection_log.info("connector sign-in: %s", email)


def forget_sign_ins() -> None:
    with _last_seen_lock:
        _last_seen.clear()


def rejected(status: int, body: bytes, who: str) -> None:
    connection_log.info("mcp request rejected: %d %s (%s)", status, reason(body), who)


def reason(body: bytes) -> str:
    """The error message from a JSON error body, else the start of the body as text."""
    text = body.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
    except ValueError:
        payload = None
        if cut := _CUT_MESSAGE.search(text):
            text = _unescape(cut.group(1))
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            error = error.get("message")
        if isinstance(error, str):
            text = error
    text = "".join(c if c.isprintable() else " " for c in text)
    text = " ".join(text.split())
    return text[:MAX_REASON_CHARS] or "(no body)"


def _unescape(value: str) -> str:
    """A JSON string's text; a value cut inside an escape keeps its raw text."""
    try:
        return json.loads(f'"{value}"')
    except ValueError:
        return value
