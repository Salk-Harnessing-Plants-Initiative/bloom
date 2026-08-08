"""`bloom_mcp.server._startup_banner` — the one-line auth-mode log at boot.

Extracted from `main()` as a pure function so the four `(api_key,
oauth_configured)` combinations are testable without invoking `main()`
itself (which validates env and calls `uvicorn.run(...)`). Added after a
review found the previous inline version only checked `api_key`, so
OAuth-configured-with-no-API-key logged a false "dev mode" claim.
"""

from __future__ import annotations

from bloom_mcp.server import _startup_banner


def test_neither_configured_is_dev_mode():
    assert _startup_banner(api_key=None, oauth_configured=False) == (
        "Bloom MCP Server starting without authentication (dev mode)"
    )


def test_api_key_only():
    assert _startup_banner(api_key="k", oauth_configured=False) == (
        "Bloom MCP Server starting with API key authentication"
    )


def test_oauth_only():
    """The case the previous version got wrong: OAuth is a real, enforced
    auth mode (MultiAuth with an empty API-key verifier list) even with no
    BLOOMMCP_API_KEY set — must not be logged as "dev mode"."""
    assert _startup_banner(api_key=None, oauth_configured=True) == (
        "Bloom MCP Server starting with OAuth login (no API key configured)"
    )


def test_both_configured():
    assert _startup_banner(api_key="k", oauth_configured=True) == (
        "Bloom MCP Server starting with OAuth login and API key authentication"
    )
