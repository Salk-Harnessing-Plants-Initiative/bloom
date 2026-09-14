"""Live check: Caddy compresses JSON through Kong and leaves bloommcp's event stream alone.

Config-shape counterpart: tests/unit/test_caddy_compression.py.
"""

import gzip
import json
import urllib.request

import pytest

from tests.integration.test_api_endpoints import SECURITY_HEADERS

pytestmark = pytest.mark.integration

# Caddy leaves a body at or under its default minimum_length uncompressed.
MIN_COMPRESSED_BYTES = 512

# Kong -> storage-api: an application/json list with one entry per bucket.
JSON_ROUTE = "/api/storage/v1/bucket"
STREAM_ROUTE = "/bloommcp/mcp"

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "bloom-ci", "version": "0"},
    },
}


def _get(base_url: str, key: str, accept_encoding: str | None = None):
    """Headers and raw (undecoded) body of the JSON route."""
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    if accept_encoding:
        headers["Accept-Encoding"] = accept_encoding
    req = urllib.request.Request(f"{base_url}{JSON_ROUTE}", headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.headers, resp.read()


def test_json_is_gzipped_and_decodes_to_the_same_data(base_url, service_role_key):
    _, plain = _get(base_url, service_role_key)
    assert len(plain) > MIN_COMPRESSED_BYTES, (
        f"{JSON_ROUTE} returned {len(plain)} bytes, too small for Caddy to compress"
    )

    headers, body = _get(base_url, service_role_key, "gzip")
    assert headers.get("Content-Encoding") == "gzip"
    assert "Accept-Encoding" in ", ".join(headers.get_all("Vary") or [])
    assert json.loads(gzip.decompress(body)) == json.loads(plain)


def test_zstd_is_preferred_when_offered(base_url, service_role_key):
    headers, _ = _get(base_url, service_role_key, "zstd, gzip")
    assert headers.get("Content-Encoding") == "zstd"


def test_nothing_is_compressed_without_accept_encoding(base_url, service_role_key):
    headers, _ = _get(base_url, service_role_key)
    assert headers.get("Content-Encoding") is None


@pytest.mark.parametrize("name,value", sorted(SECURITY_HEADERS.items()))
def test_a_compressed_response_keeps_the_security_headers(base_url, service_role_key, name, value):
    headers, _ = _get(base_url, service_role_key, "zstd, gzip")
    assert headers.get("Content-Encoding") == "zstd"
    assert headers.get_all(name) == [value]


def test_bloommcp_event_stream_is_not_compressed(base_url, bloommcp_api_key):
    if not bloommcp_api_key:
        pytest.skip("BLOOMMCP_API_KEY not configured")
    req = urllib.request.Request(
        f"{base_url}{STREAM_ROUTE}",
        data=json.dumps(INITIALIZE).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {bloommcp_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Accept-Encoding": "zstd, gzip",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        content_type = resp.headers.get("Content-Type", "")
        encoding = resp.headers.get("Content-Encoding")

    assert content_type.startswith("text/event-stream"), f"expected an event stream, got {content_type!r}"
    assert encoding is None, f"the event stream arrived compressed ({encoding})"
