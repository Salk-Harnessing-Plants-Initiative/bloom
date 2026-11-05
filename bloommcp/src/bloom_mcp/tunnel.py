"""Admits people who reach bloommcp through the Salk connector's tunnel.

AIRC signs people in with Salk and forwards their requests through a Cloudflare
tunnel to Caddy's tunnel entrance, which requires ``X-Salk-User`` and sets
``X-Bloom-Tunnel`` to ``BLOOMMCP_TUNNEL_SECRET``. Only a request carrying that
secret is a tunnel request; on any other request ``X-Salk-User`` is never read.

A tunnel request must name one ``@salk.edu`` address that belongs to an active
Bloom account (``public.bloom_user_for_email``, called as ``bloom_agent``). It
is then admitted with the shared API key in place of a caller token, and its
usage is recorded under the account's id. Queries still run as ``bloom_agent``.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import threading
import time
from collections import OrderedDict

import anyio

logger = logging.getLogger(__name__)

SECRET_ENV = "BLOOMMCP_TUNNEL_SECRET"
MIN_SECRET_LENGTH = 32
TUNNEL_HEADER = b"x-bloom-tunnel"
SALK_USER_HEADER = b"x-salk-user"
SALK_DOMAIN = "@salk.edu"
MAX_EMAIL_LENGTH = 254
LOOKUP_RPC = "bloom_user_for_email"
CACHE_TTL_SECONDS = 60
CACHE_MAX_ENTRIES = 1024
# JSON-RPC server-error code for a refused tunnel request; `data.status` holds the HTTP status.
REFUSAL_CODE = -32001
# Larger POST bodies are not read for a request id; they get the plain JSON refusal.
MAX_REFUSAL_BODY_BYTES = 1024 * 1024
# Longest error description written to the log for a failed lookup.
MAX_LOGGED_ERROR_CHARS = 300

# Printable ASCII without space: rules out whitespace and control characters.
_FIRST_ALLOWED, _LAST_ALLOWED = 0x21, 0x7E

_now = time.monotonic
_cache: OrderedDict[str, tuple[str, float]] = OrderedDict()
_cache_lock = threading.Lock()


class TunnelRefusal(Exception):
    """A tunnel request that must be refused with ``status`` and ``message``."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def jsonrpc_refusal(body: bytes, refusal: TunnelRefusal) -> bytes | None:
    """A JSON-RPC error carrying ``refusal`` for a body holding one request; None otherwise."""
    try:
        request = json.loads(body)
    except ValueError:
        return None
    request_id = request.get("id") if isinstance(request, dict) else None
    # A notification has no id; a bool is not a valid one.
    if not isinstance(request_id, (str, int)) or isinstance(request_id, bool):
        return None
    error = {
        "code": REFUSAL_CODE,
        "message": refusal.message,
        "data": {"status": refusal.status},
    }
    return json.dumps({"jsonrpc": "2.0", "id": request_id, "error": error}).encode(
        "utf-8"
    )


def validate_env() -> None:
    """Fail at startup on a tunnel secret that is too short or has no API key."""
    secret = os.environ.get(SECRET_ENV)
    if not secret:
        return
    if len(secret) < MIN_SECRET_LENGTH:
        raise RuntimeError(
            f"{SECRET_ENV} must be at least {MIN_SECRET_LENGTH} characters "
            "(generate one with `openssl rand -hex 32`)."
        )
    if not os.environ.get("BLOOMMCP_API_KEY"):
        raise RuntimeError(
            f"{SECRET_ENV} is set but BLOOMMCP_API_KEY is not; tunnel requests "
            "are admitted with the API key."
        )


def is_tunnel_request(headers: list[tuple[bytes, bytes]]) -> bool:
    """Does the request carry exactly one ``X-Bloom-Tunnel`` equal to the secret?"""
    secret = os.environ.get(SECRET_ENV)
    if not secret:
        return False
    values = [v for k, v in headers if k == TUNNEL_HEADER]
    if not values:
        return False
    if len(values) != 1 or not hmac.compare_digest(values[0], secret.encode()):
        logger.warning(
            "X-Bloom-Tunnel present but not valid; handled as a normal request"
        )
        return False
    return True


def _is_salk_address(value: str) -> bool:
    if not 0 < len(value) <= MAX_EMAIL_LENGTH:
        return False
    if any(not _FIRST_ALLOWED <= ord(c) <= _LAST_ALLOWED for c in value):
        return False
    if value.count("@") != 1:
        return False
    lowered = value.lower()
    return lowered.endswith(SALK_DOMAIN) and len(lowered) > len(SALK_DOMAIN)


def salk_user(headers: list[tuple[bytes, bytes]]) -> str:
    """The one ``@salk.edu`` address in ``X-Salk-User``, in lower case."""
    values = [v for k, v in headers if k == SALK_USER_HEADER]
    if len(values) != 1:
        logger.warning("tunnel refused: %d X-Salk-User headers", len(values))
        raise TunnelRefusal(403, "Expected exactly one X-Salk-User header.")
    value = values[0].decode("latin-1")
    if not _is_salk_address(value):
        logger.warning(
            "tunnel refused: X-Salk-User %r is not one @salk.edu address", value
        )
        raise TunnelRefusal(403, "X-Salk-User must be one @salk.edu address.")
    return value.lower()


def _cached(email: str) -> str | None:
    with _cache_lock:
        entry = _cache.get(email)
        if entry is None:
            return None
        user_id, expires_at = entry
        if _now() >= expires_at:
            del _cache[email]
            return None
        return user_id


def _remember(email: str, user_id: str) -> None:
    with _cache_lock:
        _cache[email] = (user_id, _now() + CACHE_TTL_SECONDS)
        _cache.move_to_end(email)
        while len(_cache) > CACHE_MAX_ENTRIES:
            _cache.popitem(last=False)


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def cache_size() -> int:
    with _cache_lock:
        return len(_cache)


def _query(email: str) -> str | None:
    """The account id for ``email``, or None; raises on any other result."""
    from bloom_mcp import supabase_client
    from bloom_mcp.identity import is_valid_identity

    result = supabase_client.call_rpc(LOOKUP_RPC, {"p_email": email})
    if result is None:
        return None
    if isinstance(result, str) and is_valid_identity(result):
        return result.lower()
    raise ValueError(f"unexpected {LOOKUP_RPC} result type {type(result).__name__}")


def _describe_failure(exc: Exception) -> str:
    """The error's type plus PostgREST's code and message (or the error's text); no secrets."""
    code, message = getattr(exc, "code", None), getattr(exc, "message", None)
    detail = f"{code} {message}" if code or message else str(exc)
    return f"{type(exc).__name__}: {detail}"[:MAX_LOGGED_ERROR_CHARS]


def _no_account_message(email: str) -> str:
    site_url = (os.environ.get("SITE_URL") or "").rstrip("/")
    where = f" at {site_url}" if site_url else ""
    return f"No Bloom account for {email}. Create one{where}, or reach out to the Bloom team."


async def bloom_user(email: str) -> str:
    """The Bloom account id for ``email``, from the cache or the database."""
    cached = _cached(email)
    if cached is not None:
        return cached
    try:
        user_id = await anyio.to_thread.run_sync(_query, email)
    except Exception as exc:
        logger.error("tunnel lookup failed for %s: %s", email, _describe_failure(exc))
        raise TunnelRefusal(
            503, "Could not check your Bloom account; try again shortly."
        )
    if user_id is None:
        logger.warning("tunnel refused: no Bloom account for %s", email)
        raise TunnelRefusal(403, _no_account_message(email))
    logger.info("tunnel lookup: Bloom account found for %s", email)
    _remember(email, user_id)
    return user_id


def api_key() -> str:
    """The key FastMCP's ``ApiKeyVerifier`` holds."""
    from bloom_mcp import auth

    if not auth.API_KEY:
        logger.error("tunnel refused: BLOOMMCP_API_KEY is not set")
        raise TunnelRefusal(503, "The Bloom connector is not configured.")
    return auth.API_KEY


def with_api_key(scope: dict, key: str) -> dict:
    """``scope`` with its Authorization set to the API key and the secret removed."""
    dropped = (b"authorization", TUNNEL_HEADER)
    headers = [(k, v) for k, v in scope.get("headers") or [] if k not in dropped]
    headers.append((b"authorization", f"Bearer {key}".encode("latin-1")))
    return {**scope, "headers": headers}
