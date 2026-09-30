"""Config-shape test for the /api/scrna/* edge route.

The Submit scRNA job form posts to /api/scrna/cellranger/runs, a Next.js route handler
that forwards the request to the workflows service. Caddy must send /api/scrna/* to
bloom-web, NOT to Kong: /api/* otherwise falls through to the Supabase gateway, which
owns no /scrna/* route and answers with its basic-auth catch-all. The dev stack runs no
Caddy, so this only breaks in staging/prod.

Mirrors tests/unit/test_caddy_cyl_video_route.py.
"""

from __future__ import annotations

import re

from tests.unit._caddyfile_helpers import (
    REPO_ROOT,
    block_after as _block_after,
    main_block as _main_block,
    strip_comments as _strip_comments,
    text as _text,
)


def _main() -> str:
    main = _main_block(_strip_comments(_text()))
    assert main is not None, "missing `handle @main` block in caddy/Caddyfile"
    return main


def test_scrna_api_routed_to_bloom_web_not_kong():
    block = _block_after(_main(), r"handle\s+/api/scrna/\*")
    assert block is not None, (
        "missing `handle /api/scrna/*` inside `handle @main` in caddy/Caddyfile"
    )
    assert re.search(
        r"reverse_proxy\s+bloom-web:\{\$BLOOM_WEB_PORT\}", block
    ), "/api/scrna/* must proxy to bloom-web:{$BLOOM_WEB_PORT}"
    assert "kong" not in block.lower(), "/api/scrna/* must NOT proxy to kong"


def test_scrna_api_preserves_the_api_prefix():
    """`handle_path` would strip /api and the Next.js route would 404."""
    assert "handle_path /api/scrna" not in _main(), (
        "/api/scrna/* must use `handle`, not `handle_path` — the /api prefix is "
        "part of the Next.js route path"
    )


def test_scrna_api_precedes_api_wildcard():
    main = _main()
    scrna = re.search(r"handle\s+/api/scrna/\*", main)
    wildcard = re.search(r"handle_path\s+/api/\*", main)
    assert scrna and wildcard, "both /api/scrna/* and /api/* handlers must exist in @main"
    assert scrna.start() < wildcard.start(), (
        "/api/scrna/* must be declared before the /api/* -> kong handler"
    )


def test_the_run_route_still_lives_under_api_scrna():
    """The Caddy rule and the Next.js route tree have to agree."""
    route = REPO_ROOT / "web" / "app" / "api" / "scrna" / "cellranger" / "runs" / "route.ts"
    assert route.is_file(), (
        f"expected the Cell Ranger run route at {route.relative_to(REPO_ROOT)}; "
        "if it moved, update the `handle /api/scrna/*` rule in caddy/Caddyfile"
    )
