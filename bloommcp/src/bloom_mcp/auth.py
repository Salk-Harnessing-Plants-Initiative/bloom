"""Bearer auth for the combined server and every section.

Two unrelated credentials are accepted on the same surface:

* ``BLOOMMCP_API_KEY`` — one shared static key, used service-to-service by
  langchain-agent. It identifies no individual, so it resolves to no subject.
* A Supabase-issued **OAuth access token** — obtained by a human who completed
  a browser login and consent, and the only credential that names a real user.

The API key cannot simply be replaced by OAuth: Supabase's OAuth server
advertises only the ``authorization_code`` and ``refresh_token`` grants (no
``client_credentials``), so every OAuth path requires a browser and a person to
approve. langchain-agent has neither.

Verification is HS256 against ``JWT_SECRET`` rather than JWKS because this
deployment signs symmetrically — GoTrue, PostgREST and langchain-agent all
source the same ``JWT_SECRET``. This mirrors ``bloom_mcp.identity`` and
``langchain/deps.py:get_current_user()``. Asymmetric signing keys would let
bloommcp hold only public keys, and are the natural follow-up.

Extracted into its own module so each section sub-server
(``bloom_mcp.sections.*``) can be created with the same provider without
importing ``server.py`` (which would be circular).
"""

from __future__ import annotations

import hmac
import logging
import os

from fastmcp.server.auth import AccessToken, TokenVerifier

from bloom_mcp.identity import is_valid_identity

logger = logging.getLogger(__name__)

API_KEY = os.getenv("BLOOMMCP_API_KEY")

# Public base URL of this server, and of the Supabase Auth that issues its
# tokens. Both must be reachable *by the MCP client* — a client running outside
# Docker cannot resolve internal service names — so they are configured
# explicitly rather than derived from SUPABASE_URL.
PUBLIC_URL = os.getenv("BLOOMMCP_PUBLIC_URL")
AUTHORIZATION_SERVER = os.getenv("BLOOMMCP_OAUTH_AUTHORIZATION_SERVER")

# The audience Supabase stamps on every token it issues for this project.
_AUDIENCE = "authenticated"


class ApiKeyVerifier(TokenVerifier):
    """Validates a bearer token against the shared ``BLOOMMCP_API_KEY``."""

    def __init__(self, api_key: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._api_key = api_key

    async def verify_token(self, token: str) -> AccessToken | None:
        if token and hmac.compare_digest(token, self._api_key):
            return AccessToken(token=token, client_id="bloom-client", scopes=["tools"])
        return None


class SupabaseOAuthVerifier(TokenVerifier):
    """Validates a Supabase-issued OAuth access token.

    Beyond the standard signature/audience/expiry checks, a ``client_id`` claim
    is **required**. Supabase stamps it only on tokens issued through the OAuth
    flow; an ordinary web session token has none. Without that check any
    logged-in user's session token would authenticate to bloommcp, and the
    consent step would be decorative.

    Returns ``None`` for every failure rather than raising, so an unusable
    token falls through to the other verifier instead of failing the request.
    """

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token:
            return None

        secret = os.environ.get("JWT_SECRET")
        if not secret:
            logger.warning(
                "JWT_SECRET is unset; cannot verify OAuth access tokens. "
                "Only BLOOMMCP_API_KEY authentication is available."
            )
            return None

        try:
            claims = jwt_decode(token, secret)
        except Exception:
            return None

        client_id = claims.get("client_id")
        if not client_id or not isinstance(client_id, str):
            return None

        sub = claims.get("sub")
        if not sub or not isinstance(sub, str) or not is_valid_identity(sub):
            return None

        scope = claims.get("scope")
        scopes = scope.split() if isinstance(scope, str) else []

        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=scopes,
            expires_at=claims.get("exp"),
            subject=sub.lower(),
            claims=claims,
        )


def jwt_decode(token: str, secret: str) -> dict:
    """Decode and verify, accepting `aud` as either a string or a list.

    Supabase emits a bare string on the OAuth path and a list on the session
    path; PyJWT handles both, so this is a single call kept separate only to
    keep the verifier readable.
    """
    import jwt

    return jwt.decode(token, secret, algorithms=["HS256"], audience=_AUDIENCE)


def build_auth_provider():
    """Return the auth provider for the combined server and every section.

    Three shapes, in order of configuration:

    * OAuth configured (``BLOOMMCP_PUBLIC_URL`` and
      ``BLOOMMCP_OAUTH_AUTHORIZATION_SERVER`` both set) — a ``MultiAuth`` that
      accepts an OAuth access token or the API key, and serves the
      ``/.well-known/oauth-protected-resource`` document an MCP client reads to
      discover where to log in.
    * OAuth not configured, API key set — today's behavior, unchanged.
    * Neither — ``None`` (dev mode, no authentication), unchanged.
    """
    if PUBLIC_URL and AUTHORIZATION_SERVER:
        from fastmcp.server.auth import MultiAuth, RemoteAuthProvider

        remote = RemoteAuthProvider(
            token_verifier=SupabaseOAuthVerifier(),
            authorization_servers=[AUTHORIZATION_SERVER],
            base_url=PUBLIC_URL,
            resource_name="Bloom MCP",
        )
        # The API key stays a peer credential, not a fallback that weakens
        # OAuth: each verifier independently accepts only its own shape.
        verifiers = [ApiKeyVerifier(API_KEY)] if API_KEY else []
        return MultiAuth(server=remote, verifiers=verifiers, base_url=PUBLIC_URL)

    if API_KEY:
        return ApiKeyVerifier(API_KEY)

    return None


# One provider shared by the combined server and all section sub-servers.
auth_provider = build_auth_provider()
