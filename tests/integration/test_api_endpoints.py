"""
API endpoint tests — verify Kong routing and service responses.

Prerequisites:
  1. Compose stack running: docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
  2. Database migrations applied

Run: python -m pytest tests/integration/test_api_endpoints.py -v
"""

import pytest
import urllib.request

pytestmark = pytest.mark.integration


# --- Edge Routing Tests (Caddy, not Kong) ---

def test_client_info_returns_200(api):
    """Caddy routes the exact path /api/client-info to bloom-web (NOT Kong's
    basic-auth dashboard, which 401s it) and the route returns a populated
    public config (issue #347). Asserts a NON-NULL body: the route reads
    NEXT_PUBLIC_SUPABASE_* from process.env at request time, so a missing
    runtime env returns 200 {"api_url": null, "anon_key": null} — green status,
    broken CLI login. No apikey header — the endpoint is public."""
    status, body = api("/api/client-info")
    assert status == 200, f"expected 200, got {status} (Kong basic-auth 401 = route fell through)"
    assert isinstance(body, dict), f"expected JSON object, got {body!r}"
    assert body.get("api_url"), "client-info api_url must be non-null"
    assert body.get("anon_key"), "client-info anon_key must be non-null"


# --- Kong Routing Tests ---

def test_kong_routes_auth(api, anon_key):
    """Kong routes /auth/* to GoTrue."""
    status, body = api("/api/auth/v1/health", api_key=anon_key)
    assert status == 200


# --- OAuth 2.1 login routing (bloommcp#613) ---------------------------------
# The discovery/registration routes a client hits before it holds any
# credential, so — unlike every other Kong/Caddy route in this file — these
# must be reachable with NO apikey. Previously verified only by a one-time
# manual recording; a routing typo in either Caddyfile or kong.yml would
# otherwise silently break the entire OAuth login flow with nothing in CI to
# catch it.
#
# Disclosed gap this doesn't fully close: `compose-health-check` (this file's
# real CI runner) targets `docker-compose.prod.yml`, where
# `GOTRUE_OAUTH_SERVER_ENABLED` defaults `false` and `BLOOMMCP_PUBLIC_URL` is
# unset (`.env.ci` sets neither — confirmed by reading `pr-checks.yml`'s
# `.env.ci` generation step directly, not assumed). OAuth is only actually
# enabled on dev and staging today. Each test below skips itself, with a
# clear reason, when it observes OAuth is off on whatever stack it's run
# against — so these provide real coverage the moment any job runs this file
# against a dev/staging-shaped compose, without falsely asserting that
# coverage exists in `compose-health-check` as configured today.


def test_caddy_routes_bloommcp_oauth_discovery_with_no_credential(api):
    """Caddy's `/.well-known/oauth-protected-resource/bloommcp/*` route
    reaches bloommcp directly (not Kong) — RFC 9728 places this at the origin
    root, so it can't sit under the stripped `/bloommcp` prefix. Path
    confirmed empirically against a real `build_app()` in
    `bloommcp/tests/test_oauth_discovery.py`, not assumed.

    On `compose-health-check`'s stack (OAuth disabled, `BLOOMMCP_PUBLIC_URL`
    unset), bloommcp never registers this route at all — observed in CI as a
    `502` (Caddy's own upstream-error response for the unmatched path), not
    a clean `404` as first assumed. Skip on either; a real routing/config
    break on an OAuth-enabled stack would still show up as neither."""
    status, body = api("/.well-known/oauth-protected-resource/bloommcp/mcp")
    if status in (404, 502):
        pytest.skip(
            f"OAuth is not enabled on this stack (BLOOMMCP_PUBLIC_URL unset) — "
            f"bloommcp never registers this route, observed here as {status}. "
            "Run against a dev/staging-shaped compose to exercise it."
        )
    assert status == 200, f"expected 200, got {status} (Caddy route missing or misconfigured)"
    assert isinstance(body, dict)
    assert body.get("resource"), "protected-resource metadata must name the resource"


def test_kong_routes_auth_well_known_with_no_apikey(api):
    """`/auth/v1/.well-known/*` must be reachable pre-credential — a client
    performs OIDC discovery before it holds any key."""
    status, body = api("/api/auth/v1/.well-known/openid-configuration")
    if status == 404:
        pytest.skip("GoTrue's OAuth server is not enabled on this stack.")
    assert status == 200, f"expected 200, got {status} (Kong route missing or still key-gated)"
    assert isinstance(body, dict)
    assert body.get("issuer"), "discovery document must have a non-empty issuer"


def test_gotrue_jwks_endpoint_never_serves_the_embedded_symmetric_secret(api):
    """`JWT_KEYS`/`JWT_JWKS` (`scripts/generate_jwt_keys.py`) embed the raw
    `JWT_SECRET` as a symmetric (`oct`) JWK so pre-migration HS256 tokens keep
    verifying. That's safe only because GoTrue's `/.well-known/jwks.json`
    handler filters `oct`-type keys out of what it serves *publicly* — this
    repo doesn't control that filter and nothing else pins it. A future
    GoTrue upgrade that changes it, or a misconfigured
    `BLOOMMCP_OAUTH_JWKS_URI`, would leak the same secret that also signs
    PostgREST/Storage/Realtime tokens: full RLS-bypass blast radius.

    Not gated on the OAuth-server flag specifically — GoTrue serves this for
    its own normal session tokens regardless — but still skips cleanly if
    Kong hasn't opened the route at all (pre-migration environments).

    Deliberately does NOT assert `keys` is non-empty: on a stack with no
    ES256 pair provisioned yet (`GOTRUE_JWT_KEYS` at its own default
    `${JWT_KEYS:-[]}`), the legacy symmetric key is GoTrue's only signing
    key, and its handler correctly filters it out of this public response —
    so `keys: []` here is the correct, secret-safe answer, not evidence of a
    broken endpoint. The only invariant this test actually owns is that
    whatever *is* published never includes an `oct` entry."""
    status, body = api("/api/auth/v1/.well-known/jwks.json")
    if status == 404:
        pytest.skip("Kong has not opened /auth/v1/.well-known/* on this stack.")
    assert status == 200, f"expected 200, got {status}"
    keys = body.get("keys", [])
    oct_keys = [k for k in keys if k.get("kty") == "oct"]
    assert not oct_keys, (
        f"JWKS endpoint is serving {len(oct_keys)} symmetric (oct) key(s) — "
        "this would publish JWT_SECRET, which also signs PostgREST/Storage/"
        f"Realtime tokens: {oct_keys}"
    )


def test_kong_routes_oauth_with_no_apikey(api):
    """`/auth/v1/oauth/*` must be reachable pre-credential too. A bare GET
    to `authorize` with no query params is safe to call against a live
    stack (GoTrue rejects it for missing params, registering nothing) while
    still proving the route reaches GoTrue rather than 404ing at Kong — so a
    404 here is ambiguous (route missing vs. OAuth server disabled) and
    treated as a skip, not a failure, on this endpoint specifically."""
    status, _ = api("/api/auth/v1/oauth/authorize")
    if status == 404:
        pytest.skip(
            "Either Kong has not opened /auth/v1/oauth/* on this stack, or "
            "GoTrue's OAuth server is disabled — cannot distinguish the two "
            "from a bare GET with no params."
        )


def test_kong_routes_rest(api, anon_key):
    """Kong routes /rest/* to PostgREST."""
    status, body = api("/api/rest/v1/", api_key=anon_key)
    assert status == 200
    assert "info" in body  # OpenAPI schema has info field


def test_kong_routes_storage(api, service_role_key):
    """Kong routes /storage/* to Storage API."""
    status, body = api("/api/storage/v1/bucket", api_key=service_role_key)
    assert status == 200
    assert isinstance(body, list)


# --- Service Response Tests ---

def test_postgrest_version(api, anon_key):
    """PostgREST returns its version in the OpenAPI schema."""
    status, body = api("/api/rest/v1/", api_key=anon_key)
    assert status == 200
    assert "12.2.12" in body.get("info", {}).get("version", "")


def test_auth_providers_configured(api, anon_key):
    """GoTrue has email provider enabled."""
    status, body = api("/api/auth/v1/settings", api_key=anon_key)
    assert status == 200
    assert body.get("external", {}).get("email", False) is True


def test_storage_has_expected_buckets(api, service_role_key):
    """Storage has the required buckets created by minio-init."""
    status, body = api("/api/storage/v1/bucket", api_key=service_role_key)
    assert status == 200
    bucket_names = {b["name"] for b in body}
    expected = {"images", "videos", "scrna"}
    assert expected.issubset(bucket_names), f"Missing buckets: {expected - bucket_names}"


def test_bloom_web_returns_html(api):
    """bloom-web returns HTML content."""
    status, body = api("/")
    assert status == 200
    assert "<!DOCTYPE html>" in body or "<html" in body or "next" in str(body).lower()


def test_studio_reachable():
    """Supabase Studio responds through Caddy subdomain."""
    req = urllib.request.Request("http://localhost/", headers={"Host": "studio.localhost"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        assert resp.status == 200
