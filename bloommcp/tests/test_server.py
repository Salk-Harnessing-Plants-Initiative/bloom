"""`bloom_mcp.server._startup_banner` — the one-line auth-mode log at boot."""

from __future__ import annotations

from bloom_mcp.server import _startup_banner


def test_no_api_key_is_dev_mode():
    assert _startup_banner(api_key=None) == (
        "Bloom MCP Server starting without authentication (dev mode)"
    )


def test_api_key():
    assert _startup_banner(api_key="k") == (
        "Bloom MCP Server starting with API key authentication"
    )
