"""`bloom_mcp.identity` — X-Bloom-Identity header verification.

Mirrors `langchain/deps.py:get_current_user()`'s verification exactly (PyJWT,
`algorithms=["HS256"]`, `audience="authenticated"`), plus a UUID-shape +
reserved-sentinel guard on `sub` unique to this module (see identity.py
docstring / openspec design.md Decision 1). All tests run with no live
Supabase (see conftest).
"""

from __future__ import annotations

import time
import uuid

import jwt
import pytest

from bloom_mcp.identity import (
    IdentityConfigError,
    IdentityVerificationError,
    _oauth_subject_from_scope,
    verify_identity_header,
)

SECRET = "test-jwt-secret"
A_UUID = str(uuid.uuid4())


def _token(
    sub=A_UUID,
    secret=SECRET,
    audience="authenticated",
    algorithm="HS256",
    **extra_claims,
):
    # `exp` defaults to a valid future timestamp — verify_identity_header now
    # requires the claim to exist (`options={"require": ["exp"]}`), matching
    # test_auth_oauth.py's `_oauth_token()` convention. Passing `exp=...`
    # (e.g. `_token(exp=0)`, test_expired_token_is_rejected) overrides it via
    # the dict-merge below.
    payload = {"sub": sub, "exp": int(time.time()) + 3600, **extra_claims}
    if audience is not None:
        payload["aud"] = audience
    return jwt.encode(payload, secret, algorithm=algorithm)


def test_absent_header_returns_none(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    assert verify_identity_header(None) is None
    assert verify_identity_header("") is None


def test_valid_token_resolves_sub(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    assert verify_identity_header(_token()) == A_UUID


def test_expired_token_is_rejected(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    expired = _token(exp=0)  # epoch 0 — always expired
    with pytest.raises(IdentityVerificationError):
        verify_identity_header(expired)


def test_token_with_no_exp_claim_is_rejected(monkeypatch):
    """PyJWT only validates `exp`'s *value* if present — it does not require
    the claim to exist. Without `options={"require": ["exp"]}`, an otherwise
    valid token with no `exp` claim at all would verify as never-expiring."""
    monkeypatch.setenv("JWT_SECRET", SECRET)
    no_exp = jwt.encode(
        {"sub": A_UUID, "aud": "authenticated"}, SECRET, algorithm="HS256"
    )
    with pytest.raises(IdentityVerificationError):
        verify_identity_header(no_exp)


def test_wrong_audience_is_rejected(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    with pytest.raises(IdentityVerificationError):
        verify_identity_header(_token(audience="something-else"))


def test_missing_audience_claim_is_rejected(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    with pytest.raises(IdentityVerificationError):
        verify_identity_header(_token(audience=None))


def test_wrong_signing_secret_is_rejected(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    with pytest.raises(IdentityVerificationError):
        verify_identity_header(_token(secret="a-different-secret"))


def test_malformed_token_is_rejected(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    with pytest.raises(IdentityVerificationError):
        verify_identity_header("not-a-jwt-at-all")


def test_missing_sub_claim_is_rejected(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    token = jwt.encode({"aud": "authenticated"}, SECRET, algorithm="HS256")
    with pytest.raises(IdentityVerificationError):
        verify_identity_header(token)


def test_disallowed_algorithm_is_rejected_even_with_valid_claims(monkeypatch):
    """The algorithm allow-list itself is enforced, not just accept/reject
    outcomes for otherwise-valid tokens (algorithm-confusion regression)."""
    monkeypatch.setenv("JWT_SECRET", SECRET)
    # HS384 with the correct secret and otherwise-valid claims must still be
    # rejected: only HS256 is in the allow-list.
    token = _token(algorithm="HS384")
    with pytest.raises(IdentityVerificationError):
        verify_identity_header(token)


def test_none_algorithm_is_rejected(monkeypatch):
    """A token asserting `alg: none` (no signature at all) must be rejected
    regardless of JWT_SECRET — PyJWT refuses to encode `none` without an
    explicit opt-in, so this is crafted by hand to simulate an attacker
    presenting one."""
    monkeypatch.setenv("JWT_SECRET", SECRET)
    import base64
    import json

    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "none", "typ": "JWT"}).encode()
    ).rstrip(b"=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": A_UUID, "aud": "authenticated"}).encode()
    ).rstrip(b"=")
    forged = (header + b"." + payload + b".").decode()
    with pytest.raises(IdentityVerificationError):
        verify_identity_header(forged)


def test_non_uuid_sub_is_rejected(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    with pytest.raises(IdentityVerificationError):
        verify_identity_header(_token(sub="not-a-uuid"))


def test_sub_with_trailing_newline_is_rejected(monkeypatch):
    """Regression: a `$`-anchored `.match()` (rather than `.fullmatch()`) would
    let a UUID followed by a trailing newline slip through, since `$` matches
    immediately before a trailing `\\n` as well as at the true end of string.
    `bloommcp_usage.identity` must never receive anything but a canonical
    UUID or the literal `anonymous`."""
    monkeypatch.setenv("JWT_SECRET", SECRET)
    with pytest.raises(IdentityVerificationError):
        verify_identity_header(_token(sub=A_UUID + "\n"))


def test_resolved_identity_is_normalized_to_lowercase(monkeypatch):
    """A non-lowercase UUID from any future issuer must not fragment into a
    second aggregate row for the same person."""
    monkeypatch.setenv("JWT_SECRET", SECRET)
    assert verify_identity_header(_token(sub=A_UUID.upper())) == A_UUID.lower()


def test_reserved_anonymous_sub_is_rejected(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    with pytest.raises(IdentityVerificationError):
        verify_identity_header(_token(sub="anonymous"))
    with pytest.raises(IdentityVerificationError):
        verify_identity_header(_token(sub="ANONYMOUS"))


def test_jwt_secret_unset_but_header_present_raises_config_error(monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(IdentityConfigError):
        verify_identity_header(_token())


def test_jwt_secret_unset_and_header_absent_is_fine(monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    assert verify_identity_header(None) is None


# ─── OAuth AccessToken fallback (`_oauth_subject_from_scope`) ────────────────
# add-bloommcp-oauth-usage-attribution: a second identity source for
# IdentityMiddleware's usage recording, consulted only when no
# X-Bloom-Identity header resolved one. Real mcp SDK classes are used for the
# "found a subject"/"no subject" cases so this test can't drift from what
# `BearerAuthBackend` actually writes into `scope["user"]`
# (mcp.server.auth.middleware.bearer_auth) — the access_token=None case uses a
# minimal stand-in since `AuthenticatedUser.__init__` reads `auth_info.client_id`
# immediately and cannot be constructed with `auth_info=None`.


def _authenticated_user(subject=None):
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
    from mcp.server.auth.provider import AccessToken

    token = AccessToken(token="t", client_id="c", scopes=[], subject=subject)
    return AuthenticatedUser(token)


def test_oauth_subject_from_scope_returns_the_access_tokens_subject():
    scope = {"user": _authenticated_user(subject=A_UUID)}
    assert _oauth_subject_from_scope(scope) == A_UUID


def test_oauth_subject_from_scope_returns_none_with_no_user_key():
    assert _oauth_subject_from_scope({}) is None


def test_oauth_subject_from_scope_returns_none_for_unauthenticated_user():
    from starlette.authentication import UnauthenticatedUser

    assert _oauth_subject_from_scope({"user": UnauthenticatedUser()}) is None


def test_oauth_subject_from_scope_returns_none_for_a_plain_object():
    assert _oauth_subject_from_scope({"user": object()}) is None


def test_oauth_subject_from_scope_returns_none_when_access_token_is_none():
    class _NoToken:
        access_token = None

    assert _oauth_subject_from_scope({"user": _NoToken()}) is None


def test_oauth_subject_from_scope_returns_none_for_api_key_shaped_token():
    """Matches `ApiKeyVerifier`'s real shape: an `AccessToken` with no
    `subject` — the shared key never names an individual."""
    scope = {"user": _authenticated_user(subject=None)}
    assert _oauth_subject_from_scope(scope) is None


def test_oauth_subject_from_scope_returns_none_for_empty_subject():
    scope = {"user": _authenticated_user(subject="")}
    assert _oauth_subject_from_scope(scope) is None
