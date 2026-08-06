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
