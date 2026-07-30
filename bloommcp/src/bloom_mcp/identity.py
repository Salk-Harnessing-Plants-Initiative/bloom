"""Caller identity verification via the optional ``X-Bloom-Identity`` header.

bloommcp runs every DB/Storage call as the shared ``bloom_agent`` role and
never forwards a caller's own credentials (see ``bloom_mcp.supabase_client``).
This module resolves a *verified* caller identity purely for usage tracking —
it is never used as a database/Storage authorization principal, and nothing
here changes which role performs a DB/Storage call.

Verification mirrors ``langchain/deps.py:get_current_user()`` exactly: PyJWT,
``algorithms=["HS256"]``, ``audience="authenticated"``, extracts the ``sub``
claim. One addition beyond ``deps.py``: the resolved ``sub`` must look like a
Supabase user id (a UUID) and must not equal the reserved literal
``"anonymous"`` — see ``ANONYMOUS`` below, the sentinel ``bloommcp_usage``
uses for callers with no header at all. Without this guard a validly-signed
token could collide with that sentinel and pollute or mask the aggregate
anonymous-usage count.

One deliberate divergence from ``deps.py``: ``JWT_SECRET`` is validated
lazily, only when a request actually carries the header — mirroring
``bloom_mcp.supabase_client``'s own lazy-validation convention rather than
``deps.py``'s unconditional import-time hard-fail. No current deployment
sends this header yet, so requiring the var unconditionally would force
every environment to set one for a code path nothing exercises.
"""

from __future__ import annotations

import os
import re
from contextvars import ContextVar

import jwt

ANONYMOUS = "anonymous"

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)

# Set once per request by IdentityMiddleware; read by contract.wrap.register()'s
# usage-recording wrapper. Defaults to ANONYMOUS outside of a request (e.g. at
# import time, or in a unit test that never installs the middleware).
_current_identity: ContextVar[str] = ContextVar("bloom_identity", default=ANONYMOUS)


class IdentityVerificationError(Exception):
    """A present ``X-Bloom-Identity`` header failed verification."""


class IdentityConfigError(Exception):
    """``JWT_SECRET`` is required to verify the header but is unset."""


def _is_valid_identity(sub: str) -> bool:
    """A resolved sub must be UUID-shaped and not the reserved sentinel."""
    return bool(_UUID_RE.match(sub)) and sub.lower() != ANONYMOUS


def verify_identity_header(value: str | None) -> str | None:
    """Verify an ``X-Bloom-Identity`` header value.

    Returns the resolved identity (the token's ``sub``) for a valid header, or
    ``None`` when ``value`` is absent/empty — callers should treat that as
    anonymous, not as an error.

    Raises:
        IdentityConfigError: ``JWT_SECRET`` is unset but a header was
            presented, so it cannot be verified either way.
        IdentityVerificationError: the header is present but invalid —
            bad signature, disallowed algorithm, wrong audience, expired,
            missing/malformed ``sub``, or ``sub`` equal to the reserved
            literal ``"anonymous"``.
    """
    if not value:
        return None

    secret = os.environ.get("JWT_SECRET")
    if not secret:
        raise IdentityConfigError(
            "JWT_SECRET is required to verify X-Bloom-Identity but is unset."
        )

    try:
        payload = jwt.decode(
            value, secret, algorithms=["HS256"], audience="authenticated"
        )
    except jwt.InvalidTokenError as exc:
        raise IdentityVerificationError(
            f"invalid X-Bloom-Identity token: {exc}"
        ) from None

    sub = payload.get("sub")
    if not sub or not isinstance(sub, str) or not _is_valid_identity(sub):
        raise IdentityVerificationError("X-Bloom-Identity token has no valid sub claim")
    return sub


def get_current_identity() -> str:
    """Return the resolved identity for the request currently being handled.

    Defaults to ``ANONYMOUS`` outside of a request (e.g. at import time, in a
    unit test that never installs ``IdentityMiddleware``, or when no
    ``X-Bloom-Identity`` header was present on the current request).
    """
    return _current_identity.get()


class IdentityMiddleware:
    """Raw-ASGI middleware: verifies ``X-Bloom-Identity``, rejects invalid tokens.

    A raw ASGI middleware class (``async def __call__(self, scope, receive,
    send)``), deliberately **not** ``starlette.middleware.base.BaseHTTPMiddleware``
    — this mirrors FastMCP's own ``RequestContextMiddleware`` shape (confirmed
    directly in the installed package: it's a raw ASGI class, always inserted
    as the outermost middleware of every FastMCP-built sub-app).
    ``BaseHTTPMiddleware``'s ``dispatch(request, call_next)`` pattern has a
    well-documented history of buffering responses and losing client-disconnect
    propagation for long-lived streaming responses, which matters here since
    this sits in front of the same persistent ``streamable-http``/SSE session
    FastMCP itself takes care to protect.

    Verification only runs work when the header is present, so a Docker
    healthcheck hitting ``/health`` (which never sends this header) never
    touches ``JWT_SECRET`` or PyJWT. Usage recording does **not** happen here —
    see ``bloom_mcp.usage``, which records per tool call instead, so this
    middleware only ever resolves identity and rejects invalid tokens.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        raw = dict(scope.get("headers") or []).get(b"x-bloom-identity")
        value = raw.decode("latin-1") if raw is not None else None

        try:
            identity = verify_identity_header(value)
        except IdentityConfigError as exc:
            await _json_response(scope, receive, send, status=500, error=str(exc))
            return
        except IdentityVerificationError as exc:
            await _json_response(scope, receive, send, status=401, error=str(exc))
            return

        token = _current_identity.set(identity or ANONYMOUS)
        try:
            await self.app(scope, receive, send)
        finally:
            _current_identity.reset(token)


async def _json_response(scope, receive, send, *, status: int, error: str) -> None:
    """Send a minimal JSON error response without depending on Starlette's
    higher-level ``Response`` classes, keeping this module's ASGI surface
    small and independently testable."""
    import json

    body = json.dumps({"error": error}).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})
