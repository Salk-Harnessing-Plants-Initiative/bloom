"""
Smoke tests — verify the compose stack is up and reachable.
"""

import pytest


pytestmark = pytest.mark.integration


def test_postgrest_reachable(api, anon_key):
    """PostgREST responds through Kong/nginx."""
    status, body = api("/api/rest/v1/", api_key=anon_key)
    assert status == 200
    assert "paths" in body  # OpenAPI schema


def test_auth_health(api, anon_key):
    """GoTrue auth service is healthy."""
    status, body = api("/api/auth/v1/health", api_key=anon_key)
    assert status == 200


def test_storage_reachable(api, anon_key):
    """Storage API responds."""
    status, body = api("/api/storage/v1/bucket", api_key=anon_key)
    assert status == 200


def test_bloom_web_reachable(api):
    """bloom-web frontend responds through nginx."""
    status, body = api("/")
    assert status == 200



def test_postgrest_returns_tables(api, anon_key):
    """PostgREST exposes at least one table in the public schema."""
    status, body = api("/api/rest/v1/", api_key=anon_key)
    assert status == 200
    assert "paths" in body
    # Should have at least one table endpoint besides "/"
    assert len(body["paths"]) > 1


def test_auth_returns_settings(api, anon_key):
    """GoTrue returns auth settings."""
    status, body = api("/api/auth/v1/settings", api_key=anon_key)
    assert status == 200
    assert "external" in body  # OAuth provider settings


def test_storage_lists_buckets(api, service_role_key):
    """Storage API lists buckets with service role key."""
    status, body = api("/api/storage/v1/bucket", api_key=service_role_key)
    assert status == 200
    assert isinstance(body, list)
