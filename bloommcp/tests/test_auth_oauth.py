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
import os
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


def test_partial_oauth_config_with_no_api_key_denies_rather_than_crashing(monkeypatch):
    """Same partial config, no BLOOMMCP_API_KEY either — a known shape, not a raise.

    Sets ALLOW_NO_AUTH explicitly in both directions. Inheriting conftest's suite-wide
    opt-out would leave this asserting the open shape and passing by accident.
    """
    monkeypatch.setattr(auth, "PUBLIC_URL", "https://bloom.salk.edu/bloommcp")
    monkeypatch.setattr(auth, "AUTHORIZATION_SERVER", None)
    monkeypatch.setattr(auth, "API_KEY", None)

    monkeypatch.setattr(auth, "ALLOW_NO_AUTH", False)
    assert isinstance(auth.build_auth_provider(), auth.DenyEveryone)

    monkeypatch.setattr(auth, "ALLOW_NO_AUTH", True)
    assert auth.build_auth_provider() is None


# --- Refusing to serve unauthenticated (bloom#29 pt. 3) ----------------------
#
# `validate_auth` runs from `server.main()`, not at import, matching how the
# Supabase and data-directory validators work — importing `bloom_mcp` stays
# usable with no env, while a misconfigured deploy still fails at boot.


_AUTH_ENV_KEYS = (
    "BLOOMMCP_API_KEY",
    "BLOOMMCP_PUBLIC_URL",
    "BLOOMMCP_OAUTH_AUTHORIZATION_SERVER",
    "BLOOMMCP_OAUTH_JWKS_URI",
    "BLOOMMCP_ALLOW_NO_AUTH",
    "JWT_SECRET",  # cleared too, so an ambient one can't decide the outcome
)


@pytest.fixture
def reload_auth(monkeypatch):
    """Re-import ``bloom_mcp.auth`` under a chosen env, then put it back.

    The module reads its env once at import, so these tests have to reload it.
    Reloading mutates state every other test shares, so the fixture restores
    the original environment and reloads again on teardown — without that, a
    reload here leaves later tests seeing no auth configured.
    """
    import importlib

    import bloom_mcp.auth as auth_module

    saved = {k: os.environ.get(k) for k in _AUTH_ENV_KEYS}

    def _load(**env):
        for key in _AUTH_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return importlib.reload(auth_module)

    yield _load

    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    importlib.reload(auth_module)


def test_no_auth_configured_refuses_to_start(reload_auth):
    mod = reload_auth()
    with pytest.raises(RuntimeError, match="nothing is configured"):
        mod.validate_auth()


def test_explicit_opt_out_allows_no_auth(reload_auth):
    mod = reload_auth(BLOOMMCP_ALLOW_NO_AUTH="1")
    mod.validate_auth()  # must not raise


def test_api_key_alone_satisfies_the_guard(reload_auth):
    mod = reload_auth(BLOOMMCP_API_KEY="s3cret")
    mod.validate_auth()


def test_oauth_alone_satisfies_the_guard_without_an_api_key(reload_auth):
    """OAuth-only is authenticated, so a missing API key must not fail the boot.

    JWT_SECRET is what lets it verify a token; prod sets it (docker-compose.prod.yml).
    """
    mod = reload_auth(
        BLOOMMCP_PUBLIC_URL="https://example.test/bloommcp",
        BLOOMMCP_OAUTH_AUTHORIZATION_SERVER="https://example.test/api/auth/v1",
        JWT_SECRET="signing-secret",
    )
    assert mod.auth_provider is not None
    mod.validate_auth()


def test_importing_auth_never_raises_without_env(reload_auth):
    """Import stays usable with no env — enforcement is validate_auth()'s job.

    Importing must not raise, or every test that imports the package would need
    credentials; what import must not do is leave a gate that admits anyone.
    """
    mod = reload_auth()
    assert isinstance(mod.auth_provider, mod.DenyEveryone)


# --- the guard has to be wired in, not merely present ------------------------
#
# Fresh interpreter: conftest sets BLOOMMCP_ALLOW_NO_AUTH suite-wide, so an
# in-process test cannot tell a wired guard from a deleted call site.


def _run_without_auth(body: str):
    """Run `body` in a fresh interpreter with every auth variable cleared."""
    import subprocess
    import sys

    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in (
            "BLOOMMCP_API_KEY",
            "BLOOMMCP_PUBLIC_URL",
            "BLOOMMCP_OAUTH_AUTHORIZATION_SERVER",
            "BLOOMMCP_ALLOW_NO_AUTH",
            "JWT_SECRET",
        )
    }
    return subprocess.run(
        [sys.executable, "-c", body], capture_output=True, text=True, env=env
    )


def test_main_refuses_to_serve_and_never_binds_the_port():
    """Fails if the `validate_auth()` call is removed from `main()`."""
    done = _run_without_auth(
        "import uvicorn\n"
        "uvicorn.run = lambda *a, **k: print('BOUND THE PORT')\n"
        "from bloom_mcp.server import main\n"
        "try:\n"
        "    main()\n"
        "except RuntimeError as exc:\n"
        "    print('REFUSED:', exc)\n"
    )
    assert "BOUND THE PORT" not in done.stdout, "served with no working authentication"
    # Naming the reason: main() also dies here on unset Supabase env, which would let
    # this pass with the auth check removed entirely.
    assert "nothing is configured" in done.stdout + done.stderr


def test_the_asgi_app_cannot_be_built_without_working_auth():
    """`uvicorn bloom_mcp.server:build_app` skips `main()`, as workflows is already run."""
    done = _run_without_auth(
        "from bloom_mcp.server import build_app\n"
        "try:\n"
        "    build_app()\n"
        "    print('BUILT AN UNAUTHENTICATED APP')\n"
        "except RuntimeError as exc:\n"
        "    print('REFUSED:', exc)\n"
    )
    assert "BUILT AN UNAUTHENTICATED APP" not in done.stdout
    assert "REFUSED:" in done.stdout or "RuntimeError" in done.stderr


def test_oauth_without_key_material_is_refused_too():
    """OAuth with no JWKS endpoint and no JWT_SECRET verifies nothing."""
    import subprocess
    import sys

    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("BLOOMMCP_API_KEY", "BLOOMMCP_ALLOW_NO_AUTH", "JWT_SECRET")
    }
    env["BLOOMMCP_PUBLIC_URL"] = "https://bloom.salk.edu/bloommcp"
    env["BLOOMMCP_OAUTH_AUTHORIZATION_SERVER"] = "https://bloom.salk.edu/api/auth/v1"
    env.pop("BLOOMMCP_OAUTH_JWKS_URI", None)

    done = subprocess.run(
        [
            sys.executable,
            "-c",
            "from bloom_mcp.auth import validate_auth\n"
            "try:\n"
            "    validate_auth()\n"
            "    print('ACCEPTED')\n"
            "except RuntimeError as exc:\n"
            "    print('REFUSED:', exc)\n",
        ],
        capture_output=True,
        text=True,
        env=env,
    )

    assert "ACCEPTED" not in done.stdout
    assert "no key material" in done.stdout


def test_no_configuration_denies_rather_than_leaving_no_gate(reload_auth):
    """`auth=None` is no gate at all, not a closed one.

    `server.mcp` is built at import, so anything holding it — `mcp.run()`,
    `mcp.http_app()` — serves without ever reaching validate_auth().
    """
    mod = reload_auth()

    assert mod.auth_provider is not None, "FastMCP would have had no gate"
    assert isinstance(mod.auth_provider, mod.DenyEveryone)


def test_the_deny_all_verifier_accepts_nothing(reload_auth):
    import asyncio

    verifier = reload_auth().DenyEveryone()

    for token in ("", "anything", "Bearer x", "null"):
        assert asyncio.run(verifier.verify_token(token)) is None


def test_the_explicit_opt_out_still_removes_the_gate(reload_auth):
    """`BLOOMMCP_ALLOW_NO_AUTH=1` is the one way to get an open server."""
    mod = reload_auth(BLOOMMCP_ALLOW_NO_AUTH="1")

    assert mod.auth_provider is None


def test_an_unauthenticated_request_is_rejected_over_http():
    """The property that matters, asserted end to end rather than by provider type.

    Every in-process test runs under conftest's opt-out and `reload_auth` does not
    rebuild `server.mcp`, so enforcement can only be proved in a fresh interpreter.
    """
    done = _run_without_auth(
        "import os\n"
        "os.environ.setdefault('SUPABASE_URL', 'http://localhost')\n"
        "os.environ.setdefault('SUPABASE_SERVICE_ROLE_KEY', 'x')\n"
        "from bloom_mcp.server import mcp\n"
        "from starlette.testclient import TestClient\n"
        "with TestClient(mcp.http_app(path='/mcp')) as c:\n"
        "    r = c.post('/mcp',\n"
        "        headers={'Accept': 'application/json, text/event-stream'},\n"
        "        json={'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',\n"
        "              'params': {'protocolVersion': '2025-06-18', 'capabilities': {},\n"
        "                         'clientInfo': {'name': 'x', 'version': '1'}}})\n"
        "    print('STATUS', r.status_code)\n"
        "    print('BODY', r.text[:200])\n"
    )

    assert "STATUS 401" in done.stdout, done.stdout + done.stderr
    assert "tools" not in done.stdout, "the tool surface reached an unauthenticated caller"
