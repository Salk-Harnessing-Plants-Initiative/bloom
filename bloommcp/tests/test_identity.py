"""`bloom_mcp.identity` — X-Bloom-Identity header verification.

Mirrors `langchain/deps.py:get_current_user()`'s verification exactly (PyJWT,
`algorithms=["HS256"]`, `audience="authenticated"`), plus a UUID-shape +
reserved-sentinel guard on `sub` unique to this module (see identity.py
docstring / openspec design.md Decision 1). All tests run with no live
Supabase (see conftest).
"""

from __future__ import annotations

import uuid

import jwt
import pytest

from bloom_mcp.identity import (
    ANONYMOUS,
    IdentityConfigError,
    IdentityVerificationError,
    get_current_identity,
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
    payload = {"sub": sub, **extra_claims}
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


def test_get_current_identity_defaults_to_anonymous():
    assert get_current_identity() == ANONYMOUS
