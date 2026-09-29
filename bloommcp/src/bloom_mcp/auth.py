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
# explicitly rather than derived from SUPABASE_URL. BLOOMMCP_PUBLIC_URL is also
# reused (bloom#642, storage_backend.self_serve_base_url()) as the self-serve
# base for local-backend /plots URLs — see bloommcp/docs/storage-backends.md.
# (Output artifacts don't use this at all — they surface a direct filesystem
# path for the local backend instead of a URL.)
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


class DenyEveryone(TokenVerifier):
    """Rejects every token, for when no credential is configured."""

    async def verify_token(self, token: str) -> AccessToken | None:
        return None


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
        """Verified claims, or None if the token doesn't check out.

        Tries the JWKS/ES256 verifier first when configured, then falls back
        to JWT_SECRET/HS256 rather than stopping at the first failure. GoTrue
        keeps the legacy HS256 secret in its signing-key set for exactly this
        reason across an environment's ES256 cutover ("Old HS256 tokens keep
        verifying" — see this PR's description): a token issued moments
        before the cutover is HS256-signed and still valid for up to
        JWT_EXPIRY afterward. Rejecting it the instant
        BLOOMMCP_OAUTH_JWKS_URI is set would sign that caller out mid-session
        for a reason unrelated to their own token's validity — this mirrors
        the same backward-compatibility guarantee the rest of the stack
        already commits to, rather than a new, temporary allowance.
        """
        if self._jwks_verifier is not None:
            result = await self._jwks_verifier.verify_token(token)
            if result and result.claims:
                return dict(result.claims)

        secret = os.environ.get("JWT_SECRET")
        if not secret:
            if self._jwks_verifier is None:
                logger.warning(
                    "Neither BLOOMMCP_OAUTH_JWKS_URI nor JWT_SECRET is set; cannot "
                    "verify OAuth access tokens. Only BLOOMMCP_API_KEY works."
                )
            else:
                logger.debug(
                    "OAuth token rejected by JWKS verification and JWT_SECRET is "
                    "unset, so no HS256 fallback is possible."
                )
            return None
        try:
            return jwt_decode(token, secret)
        except jwt_errors() as exc:
            logger.debug("OAuth token rejected by JWKS and HS256 verification: %s", exc)
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

    ``require: ["exp"]`` — PyJWT only validates an ``exp`` claim's *value* if
    present; it does not require the claim to exist at all. Without this, a
    token with no ``exp`` claim would verify as never-expiring. Low severity
    today (only GoTrue holds ``JWT_SECRET`` and always stamps ``exp``), but
    cheap to close rather than rely on that holding forever.
    """
    import jwt

    return jwt.decode(
        token,
        secret,
        algorithms=["HS256"],
        audience=_AUDIENCE,
        options={"require": ["exp"]},
    )


def build_auth_provider():
    """Return the auth provider for the combined server and every section.

    Three shapes, in order of configuration:

    * OAuth configured (``BLOOMMCP_PUBLIC_URL`` and
      ``BLOOMMCP_OAUTH_AUTHORIZATION_SERVER`` both set) — a ``MultiAuth`` that
      accepts an OAuth access token or the API key, and serves the
      ``/.well-known/oauth-protected-resource`` document an MCP client reads to
      discover where to log in.
    * OAuth not configured, API key set — an ``ApiKeyVerifier``.
    * Neither — a ``DenyEveryone`` that rejects every token, or ``None`` only when
      ``BLOOMMCP_ALLOW_NO_AUTH`` says so. ``None`` leaves FastMCP with no gate at all.
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

    # Deny rather than None. `auth=None` gives FastMCP no gate at all, so anything
    # holding one of these instances — `server.mcp` is built at import — would answer
    # an unauthenticated caller. validate_auth() stops a *boot*; this stops a serve.
    return None if ALLOW_NO_AUTH else DenyEveryone()


def _unverifiable_reason() -> str | None:
    """Why the configured auth cannot check a caller, or None if it can."""
    if not API_KEY and not (PUBLIC_URL and AUTHORIZATION_SERVER):
        return "nothing is configured (no BLOOMMCP_API_KEY, no OAuth pair)"
    # A provider that cannot check a signature rejects every caller: an outage that
    # reports itself healthy.
    if PUBLIC_URL and AUTHORIZATION_SERVER and not API_KEY:
        if not OAUTH_JWKS_URI and not os.getenv("JWT_SECRET"):
            return "OAuth has no key material (no BLOOMMCP_OAUTH_JWKS_URI, no JWT_SECRET)"
    return None


def validate_auth() -> None:
    """Refuse to serve unless something can actually authenticate a caller.

    Called from ``build_app()`` as well as ``main()``, so an ASGI launch cannot skip it.
    """
    reason = _unverifiable_reason()
    if reason is None:
        return
    if not ALLOW_NO_AUTH:
        raise RuntimeError(
            f"Refusing to start: {reason}. Set BLOOMMCP_API_KEY, finish the OAuth "
            f"configuration, or opt out with BLOOMMCP_ALLOW_NO_AUTH=1 (local dev only)."
        )
    logger.warning("Serving with no working authentication: %s", reason)


# One provider shared by the combined server and all section sub-servers.
auth_provider = build_auth_provider()
