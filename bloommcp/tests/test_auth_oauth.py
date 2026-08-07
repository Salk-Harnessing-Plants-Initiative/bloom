"""`bloom_mcp.auth` — Supabase OAuth access-token verification.

bloommcp accepts two unrelated credentials on the same surface: the shared
``BLOOMMCP_API_KEY`` (service-to-service, no browser) and a Supabase-issued
OAuth access token (a human who completed a browser login and consent).

The claim shapes asserted here were taken from real tokens issued by the dev
stack's Supabase Auth, not from documentation — notably that an OAuth access
token carries a ``client_id`` claim while an ordinary web session token does
not, and that ``aud`` is a bare string on the OAuth path but a list on the
session path.
"""

from __future__ import annotations

import asyncio
import time
import uuid

import jwt
import pytest

import bloom_mcp.auth as auth
from bloom_mcp.auth import ApiKeyVerifier, SupabaseOAuthVerifier

SECRET = "test-jwt-secret"
A_UUID = str(uuid.uuid4())
A_CLIENT = str(uuid.uuid4())


def _verify(verifier, token):
    return asyncio.run(verifier.verify_token(token))


def _oauth_token(
    sub=A_UUID,
    secret=SECRET,
    audience="authenticated",
    algorithm="HS256",
    client_id=A_CLIENT,
    expires_in=3600,
    **extra,
):
    payload = {
        "sub": sub,
        "role": "bloom_user",
        "scope": "email profile",
        "iat": int(time.time()),
        "exp": int(time.time()) + expires_in,
        **extra,
    }
    if audience is not None:
        payload["aud"] = audience
    if client_id is not None:
        payload["client_id"] = client_id
    return jwt.encode(payload, secret, algorithm=algorithm)


@pytest.fixture
def oauth(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    return SupabaseOAuthVerifier()


def test_valid_oauth_token_is_accepted(oauth):
    result = _verify(oauth, _oauth_token())
    assert result is not None
    assert result.client_id == A_CLIENT


def test_subject_is_the_lowercased_sub_claim(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    upper = str(uuid.uuid4()).upper()
    result = _verify(SupabaseOAuthVerifier(), _oauth_token(sub=upper))
    assert result is not None
    assert result.subject == upper.lower()


def test_claims_are_preserved_for_downstream_use(oauth):
    result = _verify(oauth, _oauth_token())
    assert result.claims["role"] == "bloom_user"
    assert result.claims["client_id"] == A_CLIENT


def test_scopes_come_from_the_scope_claim(oauth):
    result = _verify(oauth, _oauth_token(scope="email profile"))
    assert set(result.scopes) == {"email", "profile"}


def test_missing_scope_claim_yields_no_scopes(oauth):
    token = jwt.encode(
        {
            "sub": A_UUID,
            "aud": "authenticated",
            "client_id": A_CLIENT,
            "exp": int(time.time()) + 60,
        },
        SECRET,
        algorithm="HS256",
    )
    result = _verify(oauth, token)
    assert result is not None
    assert result.scopes == []


# --- The session-token guard (the reason client_id is required) --------------


def test_ordinary_session_token_is_rejected(oauth):
    """A logged-in user's web session token must not authenticate to bloommcp
    just because it verifies — it carries no client_id, meaning its holder
    never completed an MCP consent flow."""
    assert _verify(oauth, _oauth_token(client_id=None)) is None


def test_session_shaped_list_audience_without_client_id_is_rejected(oauth):
    """Real session tokens carry `aud` as a list; that difference alone must
    not become the thing that lets one through."""
    assert (
        _verify(oauth, _oauth_token(audience=["authenticated"], client_id=None)) is None
    )


def test_list_audience_with_client_id_is_accepted(oauth):
    assert _verify(oauth, _oauth_token(audience=["authenticated"])) is not None


def test_empty_client_id_is_rejected(oauth):
    assert _verify(oauth, _oauth_token(client_id="")) is None


# --- Standard JWT failure modes ---------------------------------------------


def test_expired_token_is_rejected(oauth):
    assert _verify(oauth, _oauth_token(expires_in=-10)) is None


def test_token_with_no_exp_claim_is_rejected(oauth):
    """PyJWT only validates `exp`'s *value* if present — it does not require
    the claim to exist. Without `options={"require": ["exp"]}`, an otherwise
    valid token with no `exp` claim at all would verify as never-expiring."""
    no_exp = jwt.encode(
        {"sub": A_UUID, "aud": "authenticated", "client_id": A_CLIENT},
        SECRET,
        algorithm="HS256",
    )
    assert _verify(oauth, no_exp) is None


def test_wrong_signature_is_rejected(oauth):
    assert _verify(oauth, _oauth_token(secret="not-the-secret")) is None


def test_wrong_audience_is_rejected(oauth):
    assert _verify(oauth, _oauth_token(audience="something-else")) is None


def test_missing_audience_is_rejected(oauth):
    assert _verify(oauth, _oauth_token(audience=None)) is None


def test_unsigned_token_is_rejected(oauth):
    token = jwt.encode(
        {"sub": A_UUID, "aud": "authenticated", "client_id": A_CLIENT},
        key="",
        algorithm="none",
    )
    assert _verify(oauth, token) is None


def test_garbage_is_rejected(oauth):
    assert _verify(oauth, "not-a-jwt") is None
    assert _verify(oauth, "") is None


@pytest.mark.parametrize("sub", ["", "anonymous", "not-a-uuid", A_UUID + "\n"])
def test_unusable_sub_is_rejected(oauth, sub):
    assert _verify(oauth, _oauth_token(sub=sub)) is None


def test_missing_jwt_secret_rejects_rather_than_raising(monkeypatch):
    """bloommcp must not crash when a bearer token arrives with no JWT_SECRET
    configured — the API-key path has to keep working."""
    monkeypatch.delenv("JWT_SECRET", raising=False)
    assert _verify(SupabaseOAuthVerifier(), _oauth_token()) is None


# --- JWKS -> HS256 fallback (GoTrue's own ES256-cutover backward compat) ----


def test_falls_back_to_hs256_when_jwks_verification_fails(monkeypatch):
    """A token issued moments before an environment's ES256 cutover is
    HS256-signed and still valid for up to JWT_EXPIRY afterward. GoTrue keeps
    the legacy secret in its signing-key set for exactly this reason ("Old
    HS256 tokens keep verifying") — bloommcp's own verifier must honor the
    same guarantee instead of rejecting outright the instant
    BLOOMMCP_OAUTH_JWKS_URI is configured."""
    monkeypatch.setenv("JWT_SECRET", SECRET)
    verifier = SupabaseOAuthVerifier(
        jwks_uri="https://auth.example/.well-known/jwks.json"
    )

    async def _rejects(token):
        return None  # simulates the ES256/JWKS verifier rejecting an HS256 token

    monkeypatch.setattr(verifier._jwks_verifier, "verify_token", _rejects)

    result = _verify(verifier, _oauth_token())
    assert result is not None
    assert result.client_id == A_CLIENT


def test_jwks_failure_with_no_jwt_secret_rejects_rather_than_raising(monkeypatch):
    """No fallback is possible without JWT_SECRET — must still reject
    cleanly, not raise."""
    monkeypatch.delenv("JWT_SECRET", raising=False)
    verifier = SupabaseOAuthVerifier(
        jwks_uri="https://auth.example/.well-known/jwks.json"
    )

    async def _rejects(token):
        return None

    monkeypatch.setattr(verifier._jwks_verifier, "verify_token", _rejects)

    assert _verify(verifier, _oauth_token()) is None


def test_jwks_success_is_used_without_consulting_hs256(monkeypatch):
    """When the JWKS verifier accepts a token, that result is used directly
    — JWT_SECRET is never consulted (and needn't even be set)."""
    monkeypatch.delenv("JWT_SECRET", raising=False)
    verifier = SupabaseOAuthVerifier(
        jwks_uri="https://auth.example/.well-known/jwks.json"
    )

    class _FakeResult:
        claims = {
            "sub": A_UUID,
            "client_id": A_CLIENT,
            "aud": "authenticated",
            "exp": int(time.time()) + 3600,
        }

    async def _accepts(token):
        return _FakeResult()

    monkeypatch.setattr(verifier._jwks_verifier, "verify_token", _accepts)

    result = _verify(verifier, "irrelevant-token-shape")
    assert result is not None
    assert result.client_id == A_CLIENT


# --- The API-key path is unchanged ------------------------------------------


def test_api_key_verifier_accepts_the_configured_key():
    result = _verify(ApiKeyVerifier("s3cret"), "s3cret")
    assert result is not None
    assert result.client_id == "bloom-client"


def test_api_key_verifier_rejects_anything_else():
    verifier = ApiKeyVerifier("s3cret")
    assert _verify(verifier, "wrong") is None
    assert _verify(verifier, "") is None


def test_api_key_verifier_rejects_an_oauth_token():
    """The two verifiers must not accidentally accept each other's credential."""
    assert _verify(ApiKeyVerifier("s3cret"), _oauth_token()) is None


def test_oauth_verifier_rejects_the_api_key(oauth):
    assert _verify(oauth, "s3cret") is None


# --- build_auth_provider(): partial config must not silently misconfigure ---
# `if PUBLIC_URL and AUTHORIZATION_SERVER:` requires both by design — an
# admin who sets only one (a plausible copy-paste-partial-env mistake) must
# land on a *known* fallback shape, not a half-built OAuth provider, a
# raise, or a silent drop to no-auth when a key was actually configured.


def test_public_url_without_authorization_server_falls_back_to_api_key(monkeypatch):
    monkeypatch.setattr(auth, "PUBLIC_URL", "https://bloom.salk.edu/bloommcp")
    monkeypatch.setattr(auth, "AUTHORIZATION_SERVER", None)
    monkeypatch.setattr(auth, "API_KEY", "s3cret")

    provider = auth.build_auth_provider()

    assert isinstance(provider, ApiKeyVerifier)


def test_authorization_server_without_public_url_falls_back_to_api_key(monkeypatch):
    monkeypatch.setattr(auth, "PUBLIC_URL", None)
    monkeypatch.setattr(
        auth, "AUTHORIZATION_SERVER", "https://bloom.salk.edu/api/auth/v1"
    )
    monkeypatch.setattr(auth, "API_KEY", "s3cret")

    provider = auth.build_auth_provider()

    assert isinstance(provider, ApiKeyVerifier)


def test_partial_oauth_config_with_no_api_key_is_dev_mode_not_a_crash(monkeypatch):
    """Same partial config, but no BLOOMMCP_API_KEY either — must fall
    through to `None` (dev mode), not raise or build a broken provider."""
    monkeypatch.setattr(auth, "PUBLIC_URL", "https://bloom.salk.edu/bloommcp")
    monkeypatch.setattr(auth, "AUTHORIZATION_SERVER", None)
    monkeypatch.setattr(auth, "API_KEY", None)

    assert auth.build_auth_provider() is None
