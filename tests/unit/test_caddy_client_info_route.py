"""Config-shape test for the /api/client-info edge route (issue #347).

Caddy must route the EXACT path /api/client-info to bloom-web (the Next.js
route that serves {api_url, anon_key}), NOT to Kong — otherwise it falls
through to Kong's basic-auth dashboard catch-all and returns 401. The match
must be exact so no other /api/* traffic (frontend, graviscan, plate scanners,
auth/rest/storage/realtime) is diverted from Kong.

Assertions are scoped to the `handle @main { ... }` host block via brace
matching, so the route can't false-pass by living in a non-functional block
(e.g. @minio). For the live 200 + non-null-body regression guard, see
tests/integration/test_api_endpoints.py::test_client_info_returns_200.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CADDYFILE = REPO_ROOT / "caddy" / "Caddyfile"


def _text() -> str:
    return CADDYFILE.read_text(encoding="utf-8")


def _block_after(text: str, header_pattern: str) -> str | None:
    """Body of the first `<header> {` directive, sliced by matching braces."""
    header = re.search(header_pattern, text)
    if not header:
        return None
    open_brace = text.find("{", header.end())
    if open_brace == -1:
        return None
    depth = 0
    for i in range(open_brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace + 1 : i]
    return None


def _main_block(text: str) -> str | None:
    """The body of the `handle @main { ... }` host block."""
    return _block_after(text, r"handle\s+@main\b")


def _client_info_block(text: str) -> str | None:
    """The body of the `handle /api/client-info { ... }` directive within @main."""
    main = _main_block(text)
    if main is None:
        return None
    return _block_after(main, r"handle\s+/api/client-info\b")


def test_client_info_routed_to_bloom_web_not_kong():
    block = _client_info_block(_text())
    assert block is not None, (
        "missing `handle /api/client-info` inside `handle @main` in caddy/Caddyfile"
    )
    # Assert the exact upstream — a dropped `:{$BLOOM_WEB_PORT}` would break the
    # proxy, and a substring check like `"reverse_proxy bloom-web" in block`
    # would not catch it.
    assert re.search(
        r"reverse_proxy\s+bloom-web:\{\$BLOOM_WEB_PORT\}", block
    ), "/api/client-info must proxy to bloom-web:{$BLOOM_WEB_PORT}"
    assert "kong" not in block.lower(), "/api/client-info must NOT proxy to kong"


def test_client_info_match_is_exact_not_wildcard():
    main = _main_block(_text())
    assert main is not None, "missing `handle @main` block in caddy/Caddyfile"
    # exact path only — no prefix/wildcard, and no handle_path (which would strip
    # the prefix and break the Next.js route match).
    assert "handle /api/client-info*" not in main
    assert "handle_path /api/client-info" not in main


def test_client_info_precedes_api_wildcard():
    """The exact route must sit ahead of the /api/* -> kong catch-all (documents
    intent; Caddy specificity makes ordering cosmetic, but the source order is
    the contract a reviewer reads)."""
    main = _main_block(_text())
    assert main is not None, "missing `handle @main` block in caddy/Caddyfile"
    ci = re.search(r"handle\s+/api/client-info\b", main)
    wildcard = re.search(r"handle_path\s+/api/\*", main)
    assert ci and wildcard, "both /api/client-info and /api/* handlers must exist in @main"
    assert ci.start() < wildcard.start(), (
        "/api/client-info must be declared before the /api/* -> kong handler"
    )


def test_other_api_traffic_still_goes_to_kong():
    main = _main_block(_text())
    assert main is not None, "missing `handle @main` block in caddy/Caddyfile"
    m = re.search(r"handle_path\s+/api/\*\s*\{(.*?)reverse_proxy\s+kong", main, re.DOTALL)
    assert m, "the /api/* -> kong handler must remain unchanged"
