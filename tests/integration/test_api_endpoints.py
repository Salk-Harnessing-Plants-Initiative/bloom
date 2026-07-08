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
