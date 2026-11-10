"""Live check: Caddy compresses JSON through Kong.

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
