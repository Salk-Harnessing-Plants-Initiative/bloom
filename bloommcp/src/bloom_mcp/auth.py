"""Shared Bearer-token auth for the combined server and every section.

Extracted so each section sub-server (bloom_mcp.sections.*) can be created
with the same auth provider as the main server, without importing server.py
(which would be circular).
"""

import hmac
import os

API_KEY = os.getenv("BLOOMMCP_API_KEY")


def build_auth_provider():
    """Return a token verifier when BLOOMMCP_API_KEY is set, else None (dev)."""
    if not API_KEY:
        return None

    from fastmcp.server.auth import AccessToken, TokenVerifier

    class ApiKeyVerifier(TokenVerifier):
        """Validates the Bearer token against BLOOMMCP_API_KEY."""

        async def verify_token(self, token: str) -> "AccessToken | None":
            if hmac.compare_digest(token, API_KEY):
                return AccessToken(
                    token=token, client_id="bloom-client", scopes=["tools"]
                )
            return None

    return ApiKeyVerifier()


# One provider shared by the combined server and all section sub-servers.
auth_provider = build_auth_provider()
