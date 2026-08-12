"""Storage must accept the ES256 session tokens GoTrue actually issues (#646).

`tests/unit/test_jwks_service_wiring.py` pins the compose wiring as strings. It
cannot fail the way #646 failed: the container still has to parse the JWKS and
build its accepted-`alg` list from it. That step is what broke — storage-api
derives the allowlist from the key types it parsed, so with no JWKS it stays
HS256-only and refuses every ES256 token before checking any signature.

These tests close that gap by driving the real path end to end against the live
stack: sign up through GoTrue, take the session token it issues, and present it
to Storage the way a logged-in browser does. On the pre-fix wiring the token is
refused with `"alg" (Algorithm) Header Parameter value not allowed` — a 403 no
string assertion over a compose file can produce.

Requires a stack provisioned with an asymmetric pair (`JWT_KEYS`/`JWT_JWKS`,
from `scripts/generate_jwt_keys.py`); PR CI mints one per run. Stacks still on
the shared secret skip the ES256 cases and keep the HS256 fallback case, which
must hold either way.
"""

import base64
import binascii
import json
import uuid

import pytest


pytestmark = pytest.mark.integration

# storage-api's refusal when the presented `alg` is outside its allowlist. The
# exact #646 symptom, and distinct from any signature or RLS failure.
ALG_NOT_ALLOWED = '"alg" (Algorithm) Header Parameter value not allowed'

TEST_PASSWORD = "es256TestPassword123!"


def _jwt_header(token: str) -> dict:
    """The JWT's decoded header. Signature is not checked — the stack does that."""
    encoded = token.split(".")[0]
    padded = encoded + "=" * (-len(encoded) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(padded))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        pytest.fail(f"could not decode JWT header from {token[:16]}...: {exc}")


def _message(body) -> str:
    return json.dumps(body) if isinstance(body, (dict, list)) else str(body)


@pytest.fixture
def session_token(api, anon_key, jwks_configured):
    """A real GoTrue session token for a fresh user, as a browser would hold it."""
    if not jwks_configured:
        pytest.skip("stack has no JWKS provisioned — GoTrue signs HS256 here")

    email = f"es256-{uuid.uuid4().hex[:12]}@bloom-ci.local"
    status, body = api(
        "/api/auth/v1/signup",
        api_key=anon_key,
        method="POST",
        data={"email": email, "password": TEST_PASSWORD},
    )
    assert status in (200, 201), f"signup failed: {status} {_message(body)}"

    # Autoconfirm returns the session inline; otherwise exchange the password.
    token = body.get("access_token") if isinstance(body, dict) else None
    if not token:
        status, body = api(
            "/api/auth/v1/token?grant_type=password",
            api_key=anon_key,
            method="POST",
            data={"email": email, "password": TEST_PASSWORD},
        )
        assert status == 200, f"password grant failed: {status} {_message(body)}"
        token = body.get("access_token")

    assert token, f"no access_token in GoTrue response: {_message(body)}"
    return token


def test_gotrue_signs_sessions_with_es256(session_token):
    """A provisioned stack must actually issue the token shape this fixes.

    Asserted rather than skipped on: if GoTrue silently fell back to HS256, every
    other test here would pass while covering nothing.
    """
    header = _jwt_header(session_token)
    assert header.get("alg") == "ES256", (
        f"expected an ES256 session token from a stack holding JWT_KEYS, got "
        f"{header.get('alg')!r} — GoTrue is not signing with the EC key."
    )
    assert header.get("kid"), (
        "ES256 tokens must carry a `kid` — it is what matches them to a key in "
        f"the JWKS. Header: {header}"
    )


def test_storage_accepts_the_es256_session_token(api, anon_key, session_token):
    """The #646 regression: Storage refused every ES256 token on staging."""
    status, body = api("/api/storage/v1/bucket", api_key=anon_key, bearer=session_token)
    message = _message(body)
    assert ALG_NOT_ALLOWED not in message, (
        "Storage refused the session token on its algorithm allowlist — it did "
        "not parse a JWKS, so JWT_JWKS is not reaching the container (#646). "
        f"Response: {status} {message}"
    )
    assert status == 200, f"expected 200 from Storage, got {status} {message}"


def test_signed_url_request_is_not_refused_on_algorithm(api, anon_key, session_token):
    """`createSignedUrl()` is the call users hit — every one of them 403'd.

    Asserts only on the algorithm refusal: a missing object or an RLS denial is
    a legitimate outcome here and must not be read as the bug.
    """
    status, body = api(
        f"/api/storage/v1/object/sign/images/es256-probe-{uuid.uuid4().hex[:8]}.txt",
        api_key=anon_key,
        method="POST",
        data={"expiresIn": 60},
        bearer=session_token,
    )
    message = _message(body)
    assert ALG_NOT_ALLOWED not in message, (
        f"createSignedUrl() refused on the algorithm allowlist (#646): "
        f"{status} {message}"
    )


def test_postgrest_accepts_the_es256_session_token(api, anon_key, session_token):
    """Pins the asymmetry that made #646 confusing: PostgREST took the same token.

    Also makes a Storage failure unambiguous — if this passes and Storage does
    not, the token is fine and the wiring is not.
    """
    status, body = api("/api/rest/v1/", api_key=anon_key, bearer=session_token)
    assert status == 200, f"PostgREST rejected the session token: {status} {_message(body)}"


def test_storage_still_accepts_kid_less_hs256_keys(api, anon_key):
    """The JWKS embeds JWT_SECRET so pre-migration tokens keep verifying.

    ANON_KEY has no `kid`, so it can only validate against that symmetric entry.
    Runs on every stack — provisioned or not, this must never break.
    """
    header = _jwt_header(anon_key)
    assert header.get("alg") == "HS256", f"ANON_KEY is not an HS256 token: {header}"

    status, body = api("/api/storage/v1/bucket", api_key=anon_key)
    assert status == 200, (
        "Storage rejected the kid-less HS256 anon key — the symmetric fallback "
        f"is broken: {status} {_message(body)}"
    )
