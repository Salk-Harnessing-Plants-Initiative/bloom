"""Config-shape test for the edge security headers (issue #108 item 1).

Caddy must set the security headers ONCE at site level — after the `tls`
block and before the per-host `@main`/`@studio`/`@minio` matchers — so every
hostname the stack serves inherits them from a single declaration.

Site level, not per-host, is the contract being pinned here. Reaching Supabase
Studio through Caddy does not traverse Kong, so Kong's `basic-auth` on the
`dashboard` route does not apply to that path; the anti-framing headers are
the only thing standing between an off-network attacker and an on-network
browser being used to frame an internal admin console. A well-meaning refactor
that moves this block inside `handle @main` would silently drop that, with no
error and no failing request.

Assertions locate the block by brace-matched depth rather than substring
search, so it cannot false-pass by living inside a nested `handle` (the same
reason `test_caddy_client_info_route.py` scopes to `handle @main`).

For the live end-to-end assertion — headers actually present on responses,
exactly once, with exact values — see
tests/integration/test_api_endpoints.py::test_security_headers_present.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CADDYFILE = REPO_ROOT / "caddy" / "Caddyfile"

# The exact directives the edge must emit. Values are asserted verbatim: a
# weakened value (SAMEORIGIN for DENY, unsafe-inline creeping into the CSP)
# would pass any presence-only check while changing what the browser enforces.
EXPECTED_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "\"frame-ancestors 'none'\"",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": '"camera=(), microphone=(), geolocation=()"',
}


def _text() -> str:
    return CADDYFILE.read_text(encoding="utf-8")


def _strip_comments(text: str) -> str:
    """Drop `#` comment lines so prose mentioning a directive can't satisfy an
    assertion about the directive itself."""
    return "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("#")
    )


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


def _site_block(text: str) -> str | None:
    """Body of the `{$CADDY_SITE_ADDRESSES} { ... }` site block."""
    return _block_after(text, r"\{\$CADDY_SITE_ADDRESSES\}")


def _top_level_header_blocks(site_body: str) -> list[str]:
    """Bodies of every `header { ... }` declared at depth 0 of the site block.

    Depth-aware on purpose: a `header` nested inside `handle @main { ... }`
    sits at depth 1 and must NOT be returned, otherwise this test would pass
    for exactly the regression it exists to catch.
    """
    blocks: list[str] = []
    depth = 0
    i = 0
    while i < len(site_body):
        char = site_body[i]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif depth == 0 and site_body.startswith("header", i):
            before = site_body[i - 1] if i else "\n"
            after = site_body[i + len("header") :]
            if not before.isalnum() and re.match(r"\s*\{", after):
                body = _block_after(site_body[i:], r"header\b")
                if body is not None:
                    blocks.append(body)
        i += 1
    return blocks


def test_header_block_declared_once_at_site_level():
    site = _site_block(_strip_comments(_text()))
    assert site is not None, "missing `{$CADDY_SITE_ADDRESSES}` site block in caddy/Caddyfile"

    blocks = _top_level_header_blocks(site)
    assert len(blocks) == 1, (
        f"expected exactly one site-level `header` block, found {len(blocks)}. "
        "Security headers must be declared once so every hostname inherits them."
    )


def test_all_security_headers_present_with_exact_values():
    site = _site_block(_strip_comments(_text()))
    assert site is not None, "missing site block in caddy/Caddyfile"
    blocks = _top_level_header_blocks(site)
    assert blocks, "no site-level `header` block found in caddy/Caddyfile"
    block = blocks[0]

    for name, value in EXPECTED_HEADERS.items():
        assert re.search(
            rf"^\s*{re.escape(name)}\s+{re.escape(value)}\s*$", block, re.MULTILINE
        ), f"`{name} {value}` missing from the site-level header block"


def test_headers_not_scoped_inside_a_single_host_block():
    """Guards the specific regression: moving the block inside `handle @main`.

    That would leave the main application covered — so every smoke test and
    every manual `curl` against the app still passes — while silently dropping
    the headers from Studio and the MinIO console.
    """
    stripped = _strip_comments(_text())
    for matcher in (r"handle\s+@main\b", r"handle\s+@studio\b", r"handle\s+@minio\b"):
        block = _block_after(stripped, matcher)
        if block is None:
            continue
        assert not re.search(r"^\s*header\s*\{", block, re.MULTILINE), (
            f"a `header` block is declared inside `{matcher}` — security headers "
            "belong at site level so all hostnames inherit them"
        )


def test_header_block_precedes_the_host_matchers():
    """Documents intent. Caddy's directive ordering hoists `header` above
    `handle` regardless of source position, so this is cosmetic for behaviour
    — but the source order is the contract a reviewer reads, and it is what
    makes the single declaration obviously apply to every host below it."""
    site = _site_block(_strip_comments(_text()))
    assert site is not None, "missing site block in caddy/Caddyfile"

    header = re.search(r"^\s*header\s*\{", site, re.MULTILINE)
    first_matcher = re.search(r"^\s*@(main|studio|minio)\s+host\b", site, re.MULTILINE)
    assert header and first_matcher, "expected a site-level header block and host matchers"
    assert header.start() < first_matcher.start(), (
        "the site-level `header` block must be declared before the per-host matchers"
    )


def test_hsts_not_set():
    """HSTS is deliberately absent until the exposure work.

    Browsers cache it for its full `max-age` and it cannot be withdrawn
    server-side, so an accidental addition here is materially harder to undo
    than any other header in this file — it would have to expire out of every
    client that saw it.
    """
    stripped = _strip_comments(_text())
    assert not re.search(r"Strict-Transport-Security", stripped, re.IGNORECASE), (
        "Strict-Transport-Security must not be set here — it is browser-cached "
        "and cannot be withdrawn server-side; it lands with the exposure work"
    )


def test_csp_carries_no_script_src_without_nonces():
    """`frame-ancestors` ships; `script-src` does not.

    Next.js emits inline hydration scripts, so a `script-src` without nonces
    would need `unsafe-inline` — which would not block an injected event
    handler and would be protection in name only.
    """
    site = _site_block(_strip_comments(_text()))
    assert site is not None, "missing site block in caddy/Caddyfile"
    blocks = _top_level_header_blocks(site)
    assert blocks, "no site-level header block found"
    csp = re.search(r"^\s*Content-Security-Policy\s+(.+)$", blocks[0], re.MULTILINE)
    assert csp, "Content-Security-Policy missing from the header block"
    value = csp.group(1)
    assert "unsafe-inline" not in value, (
        "a CSP with `unsafe-inline` would not block an injected handler — "
        "script-src needs Next.js nonces and belongs in its own change"
    )
