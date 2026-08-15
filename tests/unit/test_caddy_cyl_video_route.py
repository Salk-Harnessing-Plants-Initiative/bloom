"""Config-shape test for the /api/cyl/* edge route.

The on-demand scan video proxy is a Next.js route handler at
/api/cyl/experiments/{id}/scans/{id}/video. Caddy must send /api/cyl/* to
bloom-web, NOT to Kong — /api/* otherwise falls through to the Supabase
gateway, which owns no /cyl/* route and answers with its basic-auth catch-all.
The dev stack runs no Caddy, so this only breaks in staging/prod: a unit test
on the config is the cheapest place to catch it.

The match must use `handle` rather than `handle_path`: the /api prefix is part
of the Next.js route, so stripping it would 404 at the app.

Mirrors tests/unit/test_caddy_client_info_route.py, which guards the same
failure mode for /api/client-info (issue #347).
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


def _cyl_block(text: str) -> str | None:
    """The body of the `handle /api/cyl/* { ... }` directive within @main."""
    main = _main_block(text)
    if main is None:
        return None
    return _block_after(main, r"handle\s+/api/cyl/\*")


def test_cyl_api_routed_to_bloom_web_not_kong():
    block = _cyl_block(_text())
    assert block is not None, (
        "missing `handle /api/cyl/*` inside `handle @main` in caddy/Caddyfile"
    )
    # Assert the exact upstream — a dropped `:{$BLOOM_WEB_PORT}` would break the
    # proxy, and a substring check like `"reverse_proxy bloom-web" in block`
    # would not catch it.
    assert re.search(
        r"reverse_proxy\s+bloom-web:\{\$BLOOM_WEB_PORT\}", block
    ), "/api/cyl/* must proxy to bloom-web:{$BLOOM_WEB_PORT}"
    assert "kong" not in block.lower(), "/api/cyl/* must NOT proxy to kong"


def test_cyl_api_preserves_the_api_prefix():
    """`handle_path` would strip /api and the Next.js route would 404."""
    main = _main_block(_text())
    assert main is not None, "missing `handle @main` block in caddy/Caddyfile"
    assert "handle_path /api/cyl" not in main, (
        "/api/cyl/* must use `handle`, not `handle_path` — the /api prefix is "
        "part of the Next.js route path"
    )


def test_cyl_api_precedes_api_wildcard():
    """Documents intent; Caddy specificity makes ordering cosmetic, but the
    source order is the contract a reviewer reads."""
    main = _main_block(_text())
    assert main is not None, "missing `handle @main` block in caddy/Caddyfile"
    cyl = re.search(r"handle\s+/api/cyl/\*", main)
    wildcard = re.search(r"handle_path\s+/api/\*", main)
    assert cyl and wildcard, "both /api/cyl/* and /api/* handlers must exist in @main"
    assert cyl.start() < wildcard.start(), (
        "/api/cyl/* must be declared before the /api/* -> kong handler"
    )


def test_video_routes_still_live_under_api_cyl():
    """The Caddy rule and the Next.js route tree have to agree — moving one
    without the other is exactly the break this file exists to catch.

    One rule serves both routes: generating a video is experiment-scoped, while
    asking whether one exists is keyed by scan alone."""
    web_app = REPO_ROOT / "web" / "app" / "api" / "cyl"
    routes = [
        web_app
        / "experiments"
        / "[experimentId]"
        / "scans"
        / "[scanId]"
        / "video"
        / "route.ts",
        web_app / "scans" / "[scanId]" / "video" / "route.ts",
    ]
    for route in routes:
        assert route.is_file(), (
            f"expected a scan video route handler at {route.relative_to(REPO_ROOT)}; "
            "if it moved, update the `handle /api/cyl/*` rule in caddy/Caddyfile"
        )
