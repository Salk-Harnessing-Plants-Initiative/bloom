"""
Smoke tests — verify the compose stack is up and reachable.

Prerequisites:
  1. Compose stack running: docker compose -f docker-compose.prod.yml --env-file .env.prod up -d

Run: python -m pytest tests/integration/test_smoke.py -v
"""

import base64
import http.client
import os
import urllib.parse

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


def _websocket_upgrade_status(base_url: str, path: str) -> int:
    """Status of a WebSocket upgrade request; 101 means the socket opened."""
    parsed = urllib.parse.urlparse(base_url)
    conn_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(parsed.netloc, timeout=10)
    try:
        conn.request("GET", path, headers={
            "Connection": "Upgrade",
            "Upgrade": "websocket",
            "Sec-WebSocket-Version": "13",
            "Sec-WebSocket-Key": base64.b64encode(os.urandom(16)).decode(),
        })
        return conn.getresponse().status
    finally:
        conn.close()


def test_realtime_websocket_opens(base_url, anon_key):
    """Realtime accepts a socket through Kong, so its tenant resolves."""
    path = f"/api/realtime/v1/websocket?apikey={anon_key}&vsn=1.0.0"
    assert _websocket_upgrade_status(base_url, path) == 101


def test_realtime_tenant_api_refuses_anon(api, anon_key):
    """Realtime's tenant admin API is closed to the anon key."""
    status, _ = api("/api/realtime/v1/api/tenants", api_key=anon_key)
    assert status == 403
