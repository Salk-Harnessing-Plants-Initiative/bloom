"""Caller identity verification via the optional ``X-Bloom-Identity`` header.

bloommcp runs every DB/Storage call as the shared ``bloom_agent`` role and
never forwards a caller's own credentials (see ``bloom_mcp.supabase_client``).
This module resolves a *verified* caller identity purely for usage tracking —
it is never used as a database/Storage authorization principal, and nothing
here changes which role performs a DB/Storage call.

Verification mirrors ``langchain/deps.py:get_current_user()`` exactly: PyJWT,
``algorithms=["HS256"]``, ``audience="authenticated"``, extracts the ``sub``
claim. Two additions beyond ``deps.py``: the resolved ``sub`` is (a) required
to look like a Supabase user id (a UUID) and not equal the reserved literal
``"anonymous"`` — see ``ANONYMOUS`` below, the sentinel ``bloommcp_usage``
uses for callers with no header at all — and (b) normalized to lowercase, so
two different-cased spellings of the same UUID can't fragment into two
separate aggregate rows.

One deliberate divergence from ``deps.py``: ``JWT_SECRET`` is validated
lazily, only when a request actually carries the header — mirroring
``bloom_mcp.supabase_client``'s own lazy-validation convention rather than
``deps.py``'s unconditional import-time hard-fail. No current deployment
sends this header yet, so requiring the var unconditionally would force
every environment to set one for a code path nothing exercises.

**Usage recording happens in ``IdentityMiddleware`` itself, at request
granularity — not per MCP tool call.** An earlier design recorded usage via a
wrapper applied to each tool at registration time (`contract.wrap.register()`),
using a `contextvars.ContextVar` set here to carry the resolved identity into
tool-dispatch code. That design was reverted after review: for a reused MCP
``streamable-http`` session (the common case — one client session spans many
tool calls), FastMCP's ``StreamableHTTPSessionManager`` starts the actual
tool-dispatch loop in a single long-lived task once, at session creation
(``_handle_stateful_request``/``self._task_group.start(run_server)`` in the
installed ``mcp`` package); a *later* request in that session only feeds a
stream into that already-running task, never re-entering it. A `ContextVar`
set by this middleware on that later request's own (different) task cannot
reach the already-running dispatch task — `asyncio`/`anyio` task creation
snapshots context at spawn time, and that snapshot cannot see values set in
the parent context afterward. This is not fixable by threading the value
differently inside the tool-dispatch layer either: FastMCP's own
`get_http_headers()`/`get_http_request()` rely on the identical
`RequestContextMiddleware` mechanism and inherit the same limitation for a
reused session (confirmed directly: `mcp.shared.message.SessionMessage`'s
per-message metadata carries no HTTP header info to reconstruct the
*current* request from inside the persistent task). Recording directly here,
in the middleware, sidesteps the whole problem — this middleware always sees
the correct, current request, regardless of session reuse — at the cost of
attributing usage to the *request path* (which mounted surface handled it),
not the specific MCP tool name. See
openspec/changes/add-bloommcp-caller-identity/design.md Decision 4 for the
full history of this revision.
"""

from __future__ import annotations

import os
import re

import jwt

ANONYMOUS = "anonymous"

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)


class IdentityVerificationError(Exception):
    """A present ``X-Bloom-Identity`` header failed verification."""


class IdentityConfigError(Exception):
    """``JWT_SECRET`` is required to verify the header but is unset."""


def is_valid_identity(sub: str) -> bool:
    """Is this token's ``sub`` claim safe to record as a caller identity?

    Call it with the ``sub`` of an already-verified token, before writing that
    value to ``bloommcp_usage.identity``. Both credential paths use it —
    ``verify_identity_header`` for ``X-Bloom-Identity``, ``bloom_mcp.auth`` for
    OAuth access tokens — so one rule guards the column.

    Rejects two things: a ``sub`` that is not a complete UUID, and the reserved
    ``anonymous`` sentinel, which no real user may claim. ``fullmatch`` rather
    than a ``$``-anchored match because ``$`` also matches before a trailing
    newline, which would file one user under two identities.
    """
    return bool(_UUID_RE.fullmatch(sub)) and sub.lower() != ANONYMOUS


def verify_identity_header(value: str | None) -> str | None:
    """Verify an ``X-Bloom-Identity`` header value.

    Returns the resolved identity (the token's ``sub``, lowercased) for a
    valid header, or ``None`` when ``value`` is absent/empty — callers should
    treat that as anonymous, not as an error.

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
            value,
            secret,
            algorithms=["HS256"],
            audience="authenticated",
            # PyJWT only validates exp's *value* if present; it does not
            # require the claim to exist. Without this, a token with no exp
            # claim would verify as never-expiring.
            options={"require": ["exp"]},
        )
    except jwt.InvalidTokenError as exc:
        raise IdentityVerificationError(
            f"invalid X-Bloom-Identity token: {exc}"
        ) from None

    sub = payload.get("sub")
    if not sub or not isinstance(sub, str) or not is_valid_identity(sub):
        raise IdentityVerificationError("X-Bloom-Identity token has no valid sub claim")
    return sub.lower()


def _action_from_path(path: str) -> str:
    """The mounted surface a request's path resolves to: one of
    `bloom_mcp.sections.SECTIONS`'s keys, or `"combined"` for the root
    surface (`/mcp`, `/health`). Imported lazily not to avoid an import
    cycle (`server.py` already imports both `identity` and `sections`
    eagerly with no conflict) but because `bloom_mcp.sections` pulls in
    every section's tool modules — a materially heavier import graph
    (matplotlib, sleap-roots-analyze, etc.) than `identity.py` otherwise
    needs at its own import time, e.g. for a lightweight unit test of this
    module alone."""
    from bloom_mcp.sections import SECTIONS

    first_segment = path.strip("/").split("/", 1)[0]
    return first_segment if first_segment in SECTIONS else "combined"


def _oauth_subject_from_scope(scope: dict) -> str | None:
    """The verified caller's OAuth subject, if FastMCP's own bearer-auth layer
    authenticated one for this request — read from ``scope["user"]``, set by
    Starlette's ``AuthenticationMiddleware`` deeper in the same ASGI call this
    middleware wraps (see openspec add-bloommcp-oauth-usage-attribution
    design.md Decision 1 for why this is safe to read here, including for a
    reused streamable-http session).

    Returns ``None`` for every "nothing to attribute" shape — no
    ``scope["user"]`` at all (no ``auth`` configured), a non-authenticated
    value, or an ``AccessToken`` with no ``subject`` (the shared
    ``BLOOMMCP_API_KEY`` credential, via ``ApiKeyVerifier``, never sets one —
    see ``bloom_mcp.auth``) — rather than raising, mirroring
    ``verify_identity_header``'s own "absent means anonymous" contract.
    """
    user = scope.get("user")
    access_token = getattr(user, "access_token", None)
    subject = getattr(access_token, "subject", None)
    return subject or None


class IdentityMiddleware:
    """Raw-ASGI middleware: verifies ``X-Bloom-Identity`` and records usage.

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

    Verification only does real work when the header is present, so a Docker
    healthcheck hitting ``/health`` never touches ``JWT_SECRET`` or PyJWT.
    Usage recording (see module docstring for why it lives here, not in
    tool-dispatch code) is skipped for `/health` and for the `local` storage
    backend (bloom#641 — there is no Supabase to record against in fully-local
    mode, so it's skipped outright rather than attempted and failed every
    request), and is non-blocking (`bloom_mcp.usage.record_usage_async`) — it
    never adds latency to the request it's attributed to.

    Recording happens **after** the wrapped app responds, gated on the
    response not being a `401` — not before, and not unconditionally. This
    middleware only ever rejects a request itself (before reaching the
    wrapped app) for a bad *identity* header; FastMCP's own independent
    `BLOOMMCP_API_KEY` bearer check runs *inside* the wrapped app (Decision
    3) and can reject with `401` on its own. Recording unconditionally
    before delegating would log usage for a request that was never actually
    authenticated to use bloommcp at all — reachable by anyone, with no
    valid API key and no `X-Bloom-Identity` header, since this middleware is
    wired in regardless of whether any real client sends the header yet.
    Gating on the response status closes that off: an unauthenticated hit
    still gets a `401` from FastMCP as before, but is no longer recorded.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers") or []
        matches = [v for k, v in headers if k == b"x-bloom-identity"]
        if len(matches) > 1:
            await _json_response(
                scope,
                receive,
                send,
                status=401,
                error="multiple X-Bloom-Identity headers present",
            )
            return
        value = matches[0].decode("latin-1") if matches else None

        try:
            identity = verify_identity_header(value)
        except IdentityConfigError as exc:
            await _json_response(scope, receive, send, status=500, error=str(exc))
            return
        except IdentityVerificationError as exc:
            await _json_response(scope, receive, send, status=401, error=str(exc))
            return

        path = scope.get("path", "")
        should_record = path != "/health"
        response_status: dict[str, int] = {}

        async def _send(message):
            if message["type"] == "http.response.start":
                response_status["status"] = message["status"]
            await send(message)

        await self.app(scope, receive, _send if should_record else send)

        if should_record and response_status.get("status") != 401:
            from bloom_mcp.storage_backend import is_local_backend

            # Fully-local mode has no Supabase to record usage against at all —
            # skip it outright rather than attempting (and always failing) the
            # RPC (bloom#641). `record_usage_async` is imported here, inside
            # the gate, rather than above it, so local mode really does skip
            # it outright — no import of the usage module at all, not just no
            # call into it.
            if not is_local_backend():
                from bloom_mcp.usage import record_usage_async

                record_usage_async(
                    identity or _oauth_subject_from_scope(scope) or ANONYMOUS,
                    _action_from_path(path),
                )


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
