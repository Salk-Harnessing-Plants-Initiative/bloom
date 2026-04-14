"""
API endpoint tests — verify Kong routing and service responses.

Prerequisites:
  1. Compose stack running: docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
  2. Database migrations applied

Run: python -m pytest tests/integration/test_api_endpoints.py -v
"""

import pytest

pytestmark = pytest.mark.integration


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
    if not bucket_names:
        pytest.skip("No buckets registered — storage schema not initialized (expected in CI)")
    expected = {"images", "videos", "scrna"}
    assert expected.issubset(bucket_names), f"Missing buckets: {expected - bucket_names}"


def test_bloom_web_returns_html(api):
    """bloom-web returns HTML content."""
    status, body = api("/")
    assert status == 200
    assert "<!DOCTYPE html>" in body or "<html" in body or "next" in str(body).lower()


def test_studio_reachable(api):
    """Supabase Studio container is running."""
    import subprocess
    # Use plain `docker ps` to avoid needing an env-file for compose variable
    # interpolation (MINIO_DATA_PATH etc. cause parse errors without one).
    result = subprocess.run(
        ["docker", "ps", "--filter", "name=studio", "--format", "{{.Names}} {{.Status}}"],
        capture_output=True, text=True
    )
    assert result.returncode == 0, f"docker ps failed: {result.stderr}"
    assert "studio" in result.stdout.lower(), f"Studio container not found: {result.stdout}"
