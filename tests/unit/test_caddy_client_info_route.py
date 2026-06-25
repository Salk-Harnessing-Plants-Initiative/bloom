"""Config-shape test for the /api/client-info edge route (issue #347).

Caddy must route the EXACT path /api/client-info to bloom-web (the Next.js
route that serves {api_url, anon_key}), NOT to Kong — otherwise it falls
through to Kong's basic-auth dashboard catch-all and returns 401. The match
must be exact so no other /api/* traffic (frontend, graviscan, plate scanners,
auth/rest/storage/realtime) is diverted from Kong.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CADDYFILE = REPO_ROOT / "caddy" / "Caddyfile"


def _text() -> str:
    return CADDYFILE.read_text(encoding="utf-8")


def _client_info_block(text: str) -> str | None:
    """The body of the `handle /api/client-info` directive (up to the next handle)."""
    start = re.search(r"handle\s+/api/client-info\b", text)
    if not start:
        return None
    rest = text[start.end():]
    nxt = re.search(r"\n\s*(?:handle|handle_path)\b", rest)
    return rest[: nxt.start()] if nxt else rest


def test_client_info_routed_to_bloom_web_not_kong():
    block = _client_info_block(_text())
    assert block is not None, "missing `handle /api/client-info` in caddy/Caddyfile"
    assert "reverse_proxy bloom-web" in block, "/api/client-info must proxy to bloom-web"
    assert "kong" not in block, "/api/client-info must NOT proxy to kong"


def test_client_info_match_is_exact_not_wildcard():
    text = _text()
    # exact path only — no prefix/wildcard, and no handle_path (which would strip
    # the prefix and break the Next.js route match).
    assert "handle /api/client-info*" not in text
    assert "handle_path /api/client-info" not in text


def test_other_api_traffic_still_goes_to_kong():
    text = _text()
    m = re.search(r"handle_path\s+/api/\*\s*\{(.*?)reverse_proxy\s+kong", text, re.DOTALL)
    assert m, "the /api/* -> kong handler must remain unchanged"
