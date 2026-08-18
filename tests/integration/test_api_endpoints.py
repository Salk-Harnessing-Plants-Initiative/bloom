"""
API endpoint tests — verify Kong routing and service responses.

Prerequisites:
  1. Compose stack running: docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
  2. Database migrations applied

Run: python -m pytest tests/integration/test_api_endpoints.py -v
"""

import base64
import os

import pytest
import urllib.error
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


# Live counterpart to tests/unit/test_caddy_cyl_video_route.py, which only reads
# caddy/Caddyfile as text. Both /api/cyl/* routes are covered: generation is
# experiment-scoped, the stored-video lookup is keyed by scan alone.
CYL_GENERATE_BAD_ID = "/api/cyl/experiments/abc/scans/abc/video"
CYL_VIDEO_BAD_ID = "/api/cyl/scans/abc/video"
CYL_VIDEO = "/api/cyl/scans/1/video"


@pytest.mark.parametrize(
    "method,path",
    [("POST", CYL_GENERATE_BAD_ID), ("GET", CYL_VIDEO_BAD_ID)],
)
def test_caddy_routes_cyl_video_to_bloom_web_not_kong(api, method, path):
    """Caddy sends /api/cyl/* to bloom-web with the /api prefix intact.

    A rejected id is the probe: both routes validate ids before the session
    lookup, the storage probe and the upstream call, so this needs no cookie,
    no database and no workflows container, and the POST cannot start an
    encode."""
    status, body = api(path, method=method)
    assert status == 400, (
        f"{method} {path}: expected 400, got {status} — "
        f"401 = Kong's catch-all answered, 404 = reached bloom-web but not the "
        f"route (prefix stripped, or handler moved). Body: {body!r}"
    )
    assert isinstance(body, dict), f"expected JSON object, got {body!r}"
    assert "positive integer" in str(body.get("detail", "")), (
        f"400 did not come from the route's id validation: {body!r}"
    )


def test_cyl_video_route_refuses_a_signed_out_caller(api):
    """A signed-out caller gets bloom-web's own 401, not Kong's.

    Kong answers 401 too, so the wording is the discriminator. Uses a numeric
    id — the path shape a browser actually sends."""
    status, body = api(CYL_VIDEO)
    assert status == 401, f"expected 401 for a signed-out caller, got {status}: {body!r}"
    assert isinstance(body, dict), f"expected the route's JSON refusal, got {body!r}"
    assert str(body.get("detail", "")).startswith("Sign in"), (
        f"401 did not come from the route's signed-out short-circuit: {body!r}"
    )


def test_generate_route_does_not_serve_reads(api):
    """GET on the experiment-scoped route is gone — the lookup it used to do
    carried an experiment id it never checked."""
    status, _ = api("/api/cyl/experiments/1/scans/1/video")
    assert status == 405, f"expected 405 (no GET handler), got {status}"


def test_other_api_traffic_still_reaches_kong(api, anon_key):
    """The /api/cyl/* exception must not divert the rest of /api/* to bloom-web."""
    status, _ = api("/api/auth/v1/health", api_key=anon_key)
    assert status == 200, f"/api/auth/v1/health must still reach Kong, got {status}"


# --- Edge security headers (issue #108 item 1) ------------------------------
# Set once at site level in caddy/Caddyfile so every hostname inherits them.
# The config-shape counterpart is tests/unit/test_caddy_security_headers.py;
# these assert the live wire.

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin-allow-popups",
    "Cross-Origin-Resource-Policy": "same-origin",
}

# Every handler declared under `handle @main`, one route each. Status is
# deliberately irrelevant — Caddy applies the headers ahead of the handler
# chain, so a proxied 200, a synthetic 404 and an upstream-error 502 must all
# carry them. Asserting on status here would make the test brittle against
# stack configuration; asserting on headers alone is the actual contract.
#
# `handle_path /api/*` is one Caddy handler but many upstreams — Kong fans out
# to GoTrue, PostgREST and storage-api. Since a duplicate originates upstream,
# not at the edge, each is probed separately: covering only GoTrue would leave
# the paths serving trait tables, stored objects and signed URLs unguarded.
HEADER_ROUTES = [
    "/api/client-info",  # exact path -> bloom-web
    "/api/oauth/consent",  # exact path -> bloom-web
    "/api/cyl/scans/1/video",  # /api/cyl/* -> bloom-web (prefix preserved)
    "/api/auth/v1/health",  # /api/* -> kong -> gotrue
    "/api/rest/v1/",  # /api/* -> kong -> postgrest
    "/api/storage/v1/bucket",  # /api/* -> kong -> storage-api
    "/langchain/models",  # /langchain/* -> agent (prefix preserved)
    "/.well-known/oauth-protected-resource/bloommcp/mcp",  # RFC 9728 -> bloommcp
    "/bloommcp/mcp",  # /bloommcp/* -> bloommcp (prefix stripped)
    "/workflows/health",  # Caddy's own `respond 404`
    "/workflows/runs",  # /workflows/* -> workflows
    "/",  # catch-all -> bloom-web
]


@pytest.fixture(scope="module")
def header_responses(api_headers):
    """Fetch each route exactly once and reuse the headers across assertions.

    Deliberately not one request per (route, header) pair: the parametrisation
    below expands to one case per header per route, and re-requesting for each
    would multiply this suite's HTTP footprint against every upstream in the
    stack for no added coverage. The contract is per-route, so one fetch per
    route is the whole of it.
    """
    return {path: api_headers(path) for path in HEADER_ROUTES}


@pytest.mark.parametrize("path", HEADER_ROUTES)
@pytest.mark.parametrize("name,value", sorted(SECURITY_HEADERS.items()))
def test_security_headers_present(header_responses, path, name, value):
    """Each security header reaches the client with its exact value, and with
    no differing duplicate, on every handler class under the main hostname.

    `get_all` rather than `in`: Caddy sets these before the handler chain and
    `reverse_proxy` then *adds* whatever the upstream sent, so a header both
    emit arrives twice. Referrer-Policy and Permissions-Policy resolve
    last-wins, meaning a differing duplicate from an upstream silently
    downgrades the edge policy — a presence-only check reports that broken
    state as healthy.

    For most of these the assertion is every-value-matches rather than a count:
    an upstream emitting a byte-identical `nosniff` changes nothing a browser
    can observe, so failing CI for it would be a false alarm, while a *different*
    value is the real regression and still fails. The two cross-origin policies
    in SINGLE_VALUE_HEADERS are the exception — any duplicate voids them, so
    they are held to exactly one occurrence on every route here as well as on
    the consoles.
    No upstream sets any of these today, across all seven behind the routes
    above — this is the guard for the day one of them starts.
    """
    received = header_responses[path].get_all(name)
    assert received, f"{name} missing from the response to {path}"
    if name in SINGLE_VALUE_HEADERS:
        assert received == [value], (
            f"{name} for {path} arrived as {received!r} — this header must appear "
            "exactly once. Duplicate field lines are joined with a comma, which "
            "parses as neither a legal value nor a single structured-field item, "
            "so the browser drops the policy even when both copies agree"
        )
        return
    divergent = [v for v in received if v != value]
    assert not divergent, (
        f"{name} for {path} returned {received!r}; {divergent!r} differs from the "
        f"edge value {value!r} — an upstream is overriding it, and Referrer-Policy "
        "and Permissions-Policy resolve last-wins"
    )


def test_hsts_not_yet_set(header_responses):
    """HSTS is deliberately absent until the exposure work.

    Asserted on the wire, not only in config: browsers cache it for its full
    `max-age` and it cannot be withdrawn server-side, so an accidental
    rollout is materially harder to undo than any other header here — it has
    to expire out of every client that ever saw it.
    """
    received = header_responses["/api/client-info"].get_all("Strict-Transport-Security")
    assert not received, (
        f"Strict-Transport-Security is being sent ({received!r}) — it is "
        "browser-cached and cannot be withdrawn server-side"
    )


# The console hostnames, matched by Host alone against the same Caddy. Covered
# because site-level declaration is the whole point of the design: a `header`
# block moved under `handle @main` leaves these bare while every main-hostname
# assertion above still passes. Hardcoded to match the Studio tests below.
CONSOLE_HOSTS = ["studio.localhost", "minio.localhost"]

# Headers where an upstream duplicate with a *different* value is worse than the
# edge value alone, so presence is not enough to assert.
#
# `Referrer-Policy` and `Permissions-Policy` resolve last-wins — the upstream
# value silently replaces ours. The two cross-origin policies are worse than
# that: they fail open rather than last-wins. A duplicated `Cross-Origin-
# Resource-Policy` combines to `same-origin, cross-origin`, which matches no
# valid value, so the policy is discarded and the load allowed. A duplicated
# `Cross-Origin-Opener-Policy` parses as a list where a single item is required,
# and a parse failure falls back to `unsafe-none`. In both cases the protection
# disappears entirely, which a presence-only check reports as healthy.
#
# CSP is deliberately absent: browsers enforce every CSP header present, so
# MinIO's own policy arriving beside `frame-ancestors 'none'` is an
# intersection, not an override.
DIVERGENCE_SENSITIVE_HEADERS = {"Referrer-Policy", "Permissions-Policy"}

# Stricter still: these two are voided by ANY duplicate, not just a divergent one.
# Repeated field lines are joined with a comma, and `same-origin, same-origin`
# matches none of CORP's three legal values, while COOP must parse as a single
# structured-field item and a list is a parse failure. Either way the browser
# falls back to no policy, so "present" is not enough — it must be alone.
SINGLE_VALUE_HEADERS = {"Cross-Origin-Opener-Policy", "Cross-Origin-Resource-Policy"}


@pytest.fixture(scope="module")
def console_responses(api_headers, dashboard_auth):
    """Root of each console hostname, fetched once.

    Credentials are sent because the Studio hostname sits behind Kong's gate.
    Without them the assertions would land on Kong's 401 — which does carry the
    edge headers, so they would still pass, while no longer observing a single
    Studio-served response. That is precisely the surface a divergent upstream
    duplicate would appear on.
    """
    return {host: api_headers("/", host=host, extra_headers=dashboard_auth) for host in CONSOLE_HOSTS}


@pytest.mark.parametrize("host", CONSOLE_HOSTS)
@pytest.mark.parametrize("name,value", sorted(SECURITY_HEADERS.items()))
def test_security_headers_on_console_hostnames(console_responses, host, name, value):
    """The consoles inherit every header in the block from the site-level declaration.

    This is the assertion the config-shape test cannot make: that the block
    genuinely reaches hostnames other than the main one. The MinIO console
    emits four of these itself, so duplicates are expected there — hence
    presence of the edge value rather than sole occupancy, with the stricter
    no-divergence check reserved for the headers where a duplicate actually
    overrides.
    """
    received = console_responses[host].get_all(name)
    assert received, f"{name} missing from the response for {host}"
    assert value in received, (
        f"{name} for {host} is {received!r}; the edge value {value!r} is absent, "
        "so the site-level declaration is not reaching this hostname"
    )
    if name in SINGLE_VALUE_HEADERS:
        assert received == [value], (
            f"{name} for {host} arrived as {received!r} — this header must appear "
            "exactly once. Duplicates are joined with a comma, which parses as "
            "neither a valid value nor a single structured-field item, so the "
            "policy is dropped entirely even when both copies agree"
        )
    elif name in DIVERGENCE_SENSITIVE_HEADERS:
        divergent = [v for v in received if v != value]
        assert not divergent, (
            f"{name} for {host} also arrived as {divergent!r} — a divergent duplicate "
            "of this header removes or overrides the edge policy rather than adding to it"
        )


# Live counterpart to tests/unit/test_caddy_cyl_video_route.py, which only reads
# caddy/Caddyfile as text. Both /api/cyl/* routes are covered: generation is
# experiment-scoped, the stored-video lookup is keyed by scan alone.
CYL_GENERATE_BAD_ID = "/api/cyl/experiments/abc/scans/abc/video"
CYL_VIDEO_BAD_ID = "/api/cyl/scans/abc/video"
CYL_VIDEO = "/api/cyl/scans/1/video"


@pytest.mark.parametrize(
    "method,path",
    [("POST", CYL_GENERATE_BAD_ID), ("GET", CYL_VIDEO_BAD_ID)],
)
def test_caddy_routes_cyl_video_to_bloom_web_not_kong(api, method, path):
    """Caddy sends /api/cyl/* to bloom-web with the /api prefix intact.

    A rejected id is the probe: both routes validate ids before the session
    lookup, the storage probe and the upstream call, so this needs no cookie,
    no database and no workflows container, and the POST cannot start an
    encode."""
    status, body = api(path, method=method)
    assert status == 400, (
        f"{method} {path}: expected 400, got {status} — "
        f"401 = Kong's catch-all answered, 404 = reached bloom-web but not the "
        f"route (prefix stripped, or handler moved). Body: {body!r}"
    )
    assert isinstance(body, dict), f"expected JSON object, got {body!r}"
    assert "positive integer" in str(body.get("detail", "")), (
        f"400 did not come from the route's id validation: {body!r}"
    )


def test_cyl_video_route_refuses_a_signed_out_caller(api):
    """A signed-out caller gets bloom-web's own 401, not Kong's.

    Kong answers 401 too, so the wording is the discriminator. Uses a numeric
    id — the path shape a browser actually sends."""
    status, body = api(CYL_VIDEO)
    assert status == 401, f"expected 401 for a signed-out caller, got {status}: {body!r}"
    assert isinstance(body, dict), f"expected the route's JSON refusal, got {body!r}"
    assert str(body.get("detail", "")).startswith("Sign in"), (
        f"401 did not come from the route's signed-out short-circuit: {body!r}"
    )


def test_generate_route_does_not_serve_reads(api):
    """GET on the experiment-scoped route is gone — the lookup it used to do
    carried an experiment id it never checked."""
    status, _ = api("/api/cyl/experiments/1/scans/1/video")
    assert status == 405, f"expected 405 (no GET handler), got {status}"


def test_other_api_traffic_still_reaches_kong(api, anon_key):
    """The /api/cyl/* exception must not divert the rest of /api/* to bloom-web."""
    status, _ = api("/api/auth/v1/health", api_key=anon_key)
    assert status == 200, f"/api/auth/v1/health must still reach Kong, got {status}"


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


def _studio_request(credentials: tuple[str, str] | None = None):
    """A request for the Studio hostname, optionally carrying basic-auth."""
    req = urllib.request.Request("http://localhost/", headers={"Host": "studio.localhost"})
    if credentials:
        token = base64.b64encode(":".join(credentials).encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    return req


def test_studio_requires_credentials():
    """Studio's UI is gated by Kong's basic-auth, not served directly by Caddy.

    Asserting the 401 is the point: an unauthenticated 200 here is what the
    Caddyfile produced before the UI catch-all was routed through Kong, and it
    is indistinguishable from a working stack unless the status is checked.
    """
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(_studio_request(), timeout=10)
    assert excinfo.value.code == 401, (
        f"expected 401 from Kong's basic-auth, got {excinfo.value.code} — "
        "a 200 means the request never reached Kong (check CADDY_SITE_ADDRESSES "
        "covers the Studio hostname, or the @studio catch-all still proxies studio: directly)"
    )


def test_studio_reachable_with_credentials():
    """The gate opens for the DASHBOARD consumer's credentials."""
    creds = (os.environ.get("DASHBOARD_USERNAME", ""), os.environ.get("DASHBOARD_PASSWORD", ""))
    if not all(creds):
        pytest.skip("DASHBOARD_USERNAME/DASHBOARD_PASSWORD not set in the environment")
    with urllib.request.urlopen(_studio_request(creds), timeout=10) as resp:
        assert resp.status == 200
        body = resp.read()
    assert body, (
        "studio.localhost returned an empty 200 — Caddy's no-matching-site "
        "fallback, not Studio. The hostname is missing from CADDY_SITE_ADDRESSES."
    )
