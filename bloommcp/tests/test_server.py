"""`bloom_mcp.server._startup_banner` — the one-line auth-mode log at boot.

Extracted from `main()` as a pure function so the four `(api_key,
oauth_configured)` combinations are testable without invoking `main()`
itself (which validates env and calls `uvicorn.run(...)`). Added after a
review found the previous inline version only checked `api_key`, so
OAuth-configured-with-no-API-key logged a false "dev mode" claim.
"""

from __future__ import annotations

from bloom_mcp.server import _startup_banner


def test_neither_configured_names_the_opt_out_that_allowed_it():
    """`validate_auth()` refuses to boot in this state, so reaching the banner at all
    means someone set `BLOOMMCP_ALLOW_NO_AUTH`. Naming it beats "dev mode", which read
    like a harmless default on a deploy that had merely lost its key."""
    assert _startup_banner(api_key=None, oauth_configured=False) == (
        "Bloom MCP Server starting WITHOUT authentication (BLOOMMCP_ALLOW_NO_AUTH)"
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
