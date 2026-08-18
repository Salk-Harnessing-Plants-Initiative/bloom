"""Config-shape test for the edge security headers (issue #108 item 1).

Caddy must set the security headers ONCE at site level — after the `tls`
block and before the per-host `@main`/`@studio`/`@minio` matchers — so every
hostname the stack serves inherits them from a single declaration.

Site level, not per-host, is the contract being pinned here. The administrative
console hostnames have their own tracked access-control work, so the anti-framing
headers are load-bearing there in a way they are not on the main hostname. A
well-meaning refactor that moves this block inside `handle @main` would silently
drop them, with no error and no failing request.

Assertions locate directives by brace-matched depth rather than substring
search, so they cannot false-pass by living inside a nested `handle` (the same
reason `test_caddy_client_info_route.py` scopes to `handle @main`). Both Caddy
spellings are recognised — the `header { ... }` block and the single-line
`header <Field> <value>` / `header -<Field>` — because a guard that sees only
the block form reports green on exactly the downgrade it exists to catch.

For the live end-to-end assertion — headers actually present on responses,
with exact values — see
tests/integration/test_api_endpoints.py::test_security_headers_present.
"""

from __future__ import annotations

import re

from tests.unit._caddyfile_helpers import (
    block_after as _block_after,
    mask_quoted as _mask_quoted,
    site_block as _site_block,
    strip_comments as _strip_comments,
    text as _text,
)

# The exact header values the edge must emit, as they appear on the wire. Values
# are asserted verbatim: a weakened value (SAMEORIGIN for DENY, unsafe-inline
# creeping into the CSP) would pass any presence-only check while changing what
# the browser enforces. Caddyfile quoting is normalised away before comparing,
# so these match SECURITY_HEADERS in the integration test byte for byte.
EXPECTED_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin-allow-popups",
    "Cross-Origin-Resource-Policy": "same-origin",
}

# `header` as a standalone directive, never `header_up`, `header_down` or
# `request_header`.
_HEADER_TOKEN = re.compile(r"(?<![\w.-])header(?![\w.-])")
# `header_down` inside a `reverse_proxy` body sets a response header for that
# route alone. Caddy appends it after the site-level set, so it downgrades one
# hostname with every other assertion here still green.
_HEADER_DOWN_TOKEN = re.compile(r"(?<![\w.-])header_down(?![\w.-])")


def _header_directives(text: str) -> list[tuple[int, str, str]]:
    """Every `header` directive in `text`, as `(depth, form, body)`.

    `form` is `"block"` or `"single"`; `body` is the block body or the directive
    line. `depth` is brace depth relative to `text`, so callers can tell a
    site-level declaration (0) from one nested inside a `handle` (>0).
    """
    masked = _mask_quoted(text)
    found: list[tuple[int, str, str]] = []
    for match in _HEADER_TOKEN.finditer(masked):
        start = match.start()
        depth = masked.count("{", 0, start) - masked.count("}", 0, start)
        line_end = masked.find("\n", match.end())
        rest_of_line = masked[match.end() : line_end if line_end != -1 else len(masked)]
        # Caddy allows an optional matcher before the brace — `header @m { ... }`
        # or `header /path/* { ... }` — so the block form cannot be recognised by
        # what immediately follows the token. The line ending in `{` is what
        # distinguishes it.
        if rest_of_line.rstrip().endswith("{"):
            body = _block_after(text[start:], r"header\b")
            if body is not None:
                found.append((depth, "block", body))
        elif rest_of_line.strip():
            found.append((depth, "single", text[start:].splitlines()[0].strip()))
    return found


def _unquote(value: str) -> str:
    """Strip one layer of Caddy quoting, so `"x"`, `` `x` `` and `x` compare equal."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"`":
        return value[1:-1]
    return value


def _declared_headers(block: str) -> dict[str, str]:
    """`{Field: value}` from a `header` block body, quoting normalised."""
    declared = {}
    for line in block.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        # A leading + is Caddy's add prefix, not part of the field name. `-` and
        # `>` are rejected outright by test_site_block_is_unconditional_and_sets_only.
        # Splitting on whitespace also tolerates a tab-separated directive.
        name = parts[0].lstrip("+")
        declared[name] = _unquote(parts[1]) if len(parts) > 1 else ""
    return declared


def _touches_managed(directive: str) -> str | None:
    """The security header this directive sets or deletes, if any."""
    for name in EXPECTED_HEADERS:
        if re.search(rf"(?<!\w)-?{re.escape(name)}(?![\w-])", directive, re.IGNORECASE):
            return name
    # `-Field*` deletes by prefix and `-*` deletes everything, so a wildcard is
    # the cheapest way to drop a header without ever naming it.
    for prefix in re.findall(r"(?<!\w)-([\w-]*)\*", directive):
        for name in EXPECTED_HEADERS:
            if name.lower().startswith(prefix.lower()):
                return name
    return None


def _site_level_blocks(site: str) -> list[str]:
    return [body for depth, form, body in _header_directives(site)
            if depth == 0 and form == "block"]


def test_header_block_declared_once_at_site_level():
    site = _site_block(_strip_comments(_text()))
    assert site is not None, "missing `{$CADDY_SITE_ADDRESSES}` site block in caddy/Caddyfile"

    directives = _header_directives(site)
    blocks = [body for depth, form, body in directives if depth == 0 and form == "block"]
    assert len(blocks) == 1, (
        f"expected exactly one site-level `header` block, found {len(blocks)}. "
        "Security headers must be declared once so every hostname inherits them."
    )

    strays = [
        line
        for depth, form, line in directives
        if depth == 0 and form == "single" and _touches_managed(line)
    ]
    assert not strays, (
        f"a security header is also set by a single-line directive at site level: {strays!r} "
        "— Caddy applies both and the later one wins, so this silently overrides the block"
    )


def test_all_security_headers_present_with_exact_values():
    site = _site_block(_strip_comments(_text()))
    assert site is not None, "missing site block in caddy/Caddyfile"
    blocks = _site_level_blocks(site)
    assert blocks, "no site-level `header` block found in caddy/Caddyfile"

    declared = _declared_headers(blocks[0])
    for name, value in EXPECTED_HEADERS.items():
        assert name in declared, f"`{name}` missing from the site-level header block"
        assert declared[name] == value, (
            f"`{name}` is {declared[name]!r} in caddy/Caddyfile, expected {value!r}"
        )


def test_headers_not_set_below_site_level():
    """No security header may be set or deleted anywhere below site level.

    Scans every nested block rather than an enumerated list of hostnames, so a
    renamed matcher (`@studio` to `@studio-ui`), a `handle_errors`, or a bare
    `handle /path/*` container cannot slip past by not being on the list. A
    `header_down` inside a `reverse_proxy` body counts too: Caddy appends it
    after the site-level set, leaving that host with two values.
    """
    site = _site_block(_strip_comments(_text()))
    assert site is not None, "missing site block in caddy/Caddyfile"

    for depth, form, body in _header_directives(site):
        if depth == 0:
            continue
        managed = _touches_managed(body)
        assert managed is None, (
            f"a nested {form} `header` directive touches {managed} "
            f"({body.strip().splitlines()[0]!r}) — the security headers belong at "
            "site level so every hostname inherits them; anything below overrides "
            "the edge for one host alone, with no error"
        )

    for down in _HEADER_DOWN_TOKEN.finditer(_mask_quoted(site)):
        line = site[down.start():].splitlines()[0].strip()
        managed = _touches_managed(line)
        assert managed is None, (
            f"a `header_down` sets {managed} ({line!r}) — Caddy appends it after "
            "the site-level value, so that route ends up with two values"
        )


def test_site_block_is_unconditional_and_sets_only():
    """The site-level block must set, never defer and never delete.

    A `>` prefix on any field — or an explicit `defer` — defers the whole block
    until after the handler chain, which skips responses Caddy generates itself.
    An upstream-error 502 would then carry no security headers at all, which is
    the opposite of the stated contract. A `-` deletion inside the block removes
    a header the same block is meant to set.
    """
    site = _site_block(_strip_comments(_text()))
    assert site is not None, "missing site block in caddy/Caddyfile"
    blocks = _site_level_blocks(site)
    assert blocks, "no site-level `header` block found"

    for line in (l.strip() for l in blocks[0].splitlines()):
        if not line:
            continue
        assert not line.startswith(">"), (
            f"{line!r} defers the whole block — deferred headers are skipped on "
            "responses Caddy generates itself, so a 502 would carry none of them"
        )
        assert line.split()[0] != "defer", (
            "`defer` skips responses Caddy generates itself, so upstream-error "
            "responses would carry no security headers"
        )
        assert not line.startswith("-"), (
            f"{line!r} deletes a header inside the block that sets them; a "
            "wildcard like `-*` removes every one while each name still reads present"
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
    blocks = _site_level_blocks(site)
    assert blocks, "no site-level header block found"

    value = _declared_headers(blocks[0]).get("Content-Security-Policy")
    assert value, "Content-Security-Policy missing from the header block"
    assert "unsafe-inline" not in value, (
        "a CSP with `unsafe-inline` would not block an injected handler — "
        "script-src needs Next.js nonces and belongs in its own change"
    )
    assert "script-src" not in value, (
        "script-src requires Next.js nonce middleware to be worth anything; "
        "it belongs in its own change, not smuggled in beside frame-ancestors"
    )
