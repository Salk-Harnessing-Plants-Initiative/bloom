"""Config-shape test for the /api/gravi/* edge route.

The plate video route handlers will live under /api/gravi on bloom-web. Caddy
must send /api/gravi/* there and not to Kong — /api/* otherwise falls through
to the Supabase gateway, which owns no /gravi/* route and answers with its
basic-auth catch-all. The dev stack runs no Caddy, so this only breaks in
staging: a unit test on the config is the cheapest place to catch it.

The match must use `handle` rather than `handle_path`: the /api prefix is part
of the Next.js route, so stripping it would 404 at the app.

Mirrors tests/unit/test_caddy_cyl_video_route.py, which guards the same failure
mode for /api/cyl/*. That file also asserts its route handlers exist on disk;
the gravi ones land with the proxy routes, so there is nothing to point at yet.
"""

from __future__ import annotations

import re

from tests.unit._caddyfile_helpers import (
    block_after as _block_after,
    main_block as _main_block,
    strip_comments as _strip_comments,
    text as _text,
)


def _gravi_block(text: str) -> str | None:
    """The body of the `handle /api/gravi/* { ... }` directive within @main."""
    main = _main_block(_strip_comments(text))
    if main is None:
        return None
    return _block_after(main, r"handle\s+/api/gravi/\*")


def test_gravi_api_routed_to_bloom_web_not_kong():
    block = _gravi_block(_text())
    assert block is not None, (
        "missing `handle /api/gravi/*` inside `handle @main` in caddy/Caddyfile"
    )
    # The exact upstream: a dropped `:{$BLOOM_WEB_PORT}` would break the proxy,
    # and a substring check for `reverse_proxy bloom-web` would not catch it.
    assert re.search(
        r"reverse_proxy\s+bloom-web:\{\$BLOOM_WEB_PORT\}", block
    ), "/api/gravi/* must proxy to bloom-web:{$BLOOM_WEB_PORT}"
    assert "kong" not in block.lower(), "/api/gravi/* must NOT proxy to kong"


def test_gravi_api_preserves_the_api_prefix():
    """`handle_path` would strip /api and the Next.js route would 404."""
    main = _main_block(_strip_comments(_text()))
    assert main is not None, "missing `handle @main` block in caddy/Caddyfile"
    assert "handle_path /api/gravi" not in main, (
        "/api/gravi/* must use `handle`, not `handle_path` — the /api prefix is "
        "part of the Next.js route path"
    )


def test_gravi_api_precedes_api_wildcard():
    """Documents intent; Caddy specificity makes ordering cosmetic, but the
    source order is the contract a reviewer reads."""
    main = _main_block(_strip_comments(_text()))
    assert main is not None, "missing `handle @main` block in caddy/Caddyfile"
    gravi = re.search(r"handle\s+/api/gravi/\*", main)
    wildcard = re.search(r"handle_path\s+/api/\*", main)
    assert gravi and wildcard, "both /api/gravi/* and /api/* handlers must exist in @main"
    assert gravi.start() < wildcard.start(), (
        "/api/gravi/* must be declared before the /api/* -> kong handler"
    )
