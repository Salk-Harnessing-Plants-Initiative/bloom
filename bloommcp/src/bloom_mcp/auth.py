"""Decides whether a request may use bloommcp at all.

Every request arrives with an ``Authorization: Bearer <token>`` header, checked
against ``BLOOMMCP_API_KEY``, one shared static key used service-to-service. It
names no individual: callers are identified by ``X-Bloom-Identity`` or, for the
Salk connector, by the tunnel (``bloom_mcp.identity`` and ``bloom_mcp.tunnel``).

It decides **admission only, never data access**: every database and Storage
call runs as ``bloom_agent`` regardless of who authenticated, and the caller's
token is never forwarded (see ``bloom_mcp.supabase_client``).

Separate module so every section sub-server (``bloom_mcp.sections.*``) shares
one provider without importing ``server.py``, which would be circular.
"""

from __future__ import annotations

import hmac
import os

from fastmcp.server.auth import AccessToken, TokenVerifier

API_KEY = os.getenv("BLOOMMCP_API_KEY")


class ApiKeyVerifier(TokenVerifier):
    """Validates a bearer token against the shared ``BLOOMMCP_API_KEY``."""

    def __init__(self, api_key: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._api_key = api_key

    async def verify_token(self, token: str) -> AccessToken | None:
        if token and hmac.compare_digest(token, self._api_key):
            return AccessToken(token=token, client_id="bloom-client", scopes=["tools"])
        return None


def build_auth_provider():
    """The API-key verifier when ``BLOOMMCP_API_KEY`` is set, else ``None`` (dev
    mode, no authentication)."""
    if API_KEY:
        return ApiKeyVerifier(API_KEY)
    return None


# One provider shared by the combined server and all section sub-servers.
auth_provider = build_auth_provider()
