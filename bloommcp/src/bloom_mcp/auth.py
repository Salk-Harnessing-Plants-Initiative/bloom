"""Decides whether a request may use bloommcp at all, and who is making it.

Every request arrives with an ``Authorization: Bearer <token>`` header. This
module verifies that token and either rejects the request or reports the
caller's identity. Two kinds of token are accepted:

* ``BLOOMMCP_API_KEY`` — one shared static key, used service-to-service.
Names no individual.
* A Supabase OAuth access token — issued to an external MCP client after a
  human completed a browser login and consent. Names the user, in ``sub``.

It decides **admission only, never data access**: every database and Storage
call runs as ``bloom_agent`` regardless of who authenticated, and the caller's
token is never forwarded (see ``bloom_mcp.supabase_client``).

Separate module so every section sub-server (``bloom_mcp.sections.*``) shares
one provider without importing ``server.py``, which would be circular.
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

# Where to fetch Supabase's public signing keys. Set this once Supabase signs
# asymmetrically (ES256), which is required for it to issue ID tokens at all —
# a client requesting the `openid` scope fails outright against a shared HS256
# secret. Leave unset while Supabase still signs with the shared secret, and
# tokens are verified with JWT_SECRET as before.
OAUTH_JWKS_URI = os.getenv("BLOOMMCP_OAUTH_JWKS_URI")

# The audience Supabase stamps on every token it issues for this project.
_AUDIENCE = "authenticated"

# Explicit opt-out for running with no authentication at all. Required because
# an unset BLOOMMCP_API_KEY used to mean "no auth" silently, so a deploy that
# lost the secret served every tool to anyone who could reach the port.
ALLOW_NO_AUTH = os.getenv("BLOOMMCP_ALLOW_NO_AUTH", "").lower() in (
    "1",
    "true",
    "yes",
)


class ApiKeyVerifier(TokenVerifier):
    """Validates a bearer token against the shared ``BLOOMMCP_API_KEY``.

    OAuth cannot replace this key: Supabase advertises only the
    ``authorization_code`` and ``refresh_token`` grants, so every OAuth path
    needs a browser and a person to approve. langchain-agent has neither.
    """

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

    Signature checking is delegated: to FastMCP's ``JWTVerifier`` against
    Supabase's JWKS when ``BLOOMMCP_OAUTH_JWKS_URI`` is set (it handles key
    fetching, caching and rotation), otherwise to ``JWT_SECRET`` directly. The
    claim rules below apply either way.

    Returns ``None`` for every failure rather than raising, so an unusable
    token falls through to the other verifier instead of failing the request.
    """

    def __init__(self, jwks_uri: str | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._jwks_uri = jwks_uri
        self._jwks_verifier = None
        if jwks_uri:
            from fastmcp.server.auth.providers.jwt import JWTVerifier

            self._jwks_verifier = JWTVerifier(
                jwks_uri=jwks_uri, audience=_AUDIENCE, algorithm="ES256"
            )

    async def _claims(self, token: str) -> dict | None:
        """Verified claims, or None if the token doesn't check out."""
        if self._jwks_verifier is not None:
            result = await self._jwks_verifier.verify_token(token)
            return dict(result.claims) if result and result.claims else None

        secret = os.environ.get("JWT_SECRET")
        if not secret:
            logger.warning(
                "Neither BLOOMMCP_OAUTH_JWKS_URI nor JWT_SECRET is set; cannot "
                "verify OAuth access tokens. Only BLOOMMCP_API_KEY works."
            )
            return None
        try:
            return jwt_decode(token, secret)
        except jwt_errors() as exc:
            logger.debug("OAuth token rejected: %s", exc)
            return None

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token:
            return None

        claims = await self._claims(token)
        if not claims:
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


def jwt_errors() -> tuple[type[Exception], ...]:
    """The exceptions a bad token raises, so a genuine bug in verification
    surfaces instead of being swallowed as `token rejected`."""
    import jwt

    return (jwt.InvalidTokenError,)


def jwt_decode(token: str, secret: str) -> dict:
    """Decode and verify a Supabase JWT.

    HS256 against ``JWT_SECRET``, not JWKS, because this deployment signs
    symmetrically — GoTrue, PostgREST and langchain-agent all source the same
    secret. Mirrors ``bloom_mcp.identity`` and ``langchain/deps.py``.

    ``aud`` arrives as a bare string on the OAuth path and a list on the
    session path; PyJWT accepts either.
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
            token_verifier=SupabaseOAuthVerifier(jwks_uri=OAUTH_JWKS_URI),
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


def validate_auth() -> None:
    """Refuse to serve with no authentication unless explicitly opted out.

    Called from ``server.main()`` rather than at import, matching how the
    Supabase and data-directory validators work: importing ``bloom_mcp`` stays
    side-effect-free for the unit tests, while a misconfigured deploy still
    fails at boot instead of quietly serving every tool unauthenticated.
    """
    if auth_provider is not None:
        return
    if not ALLOW_NO_AUTH:
        raise RuntimeError(
            "No authentication is configured: BLOOMMCP_API_KEY is unset and "
            "OAuth is not configured (BLOOMMCP_PUBLIC_URL and "
            "BLOOMMCP_OAUTH_AUTHORIZATION_SERVER). Refusing to start, because "
            "this would serve every tool to any caller that can reach the "
            "port. Set BLOOMMCP_API_KEY, configure OAuth, or — for local "
            "development only — opt out with BLOOMMCP_ALLOW_NO_AUTH=1."
        )
    logger.warning(
        "BLOOMMCP_ALLOW_NO_AUTH is set: serving with NO authentication. "
        "Local development only — never staging or production."
    )


# One provider shared by the combined server and all section sub-servers.
auth_provider = build_auth_provider()
