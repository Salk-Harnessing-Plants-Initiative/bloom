"""Config-shape test: hostnames with no route must be refused, not answered.

`CADDY_SITE_ADDRESSES` carries a wildcard, so the certificate covers every
one-level subdomain and Caddy accepts the connection for any of them. A name
with no `handle @host` block falls out of the site block, and Caddy's implicit
response to that is an empty `200 OK` — a success code for a page we do not
serve. Anything checking only the status reads it as healthy, which is how
`test_studio_reachable` came to pass against nothing (see #649).

Over HTTPS a name outside the wildcard is refused at the handshake, since no
certificate covers it. Plain HTTP has no such step, hence the `:80` block.

For the live assertion see tests/integration/test_caddy_unrouted_host.py.
"""

from __future__ import annotations

import re
from pathlib import Path

CADDYFILE = Path(__file__).resolve().parents[2] / "caddy" / "Caddyfile"


def _text() -> str:
    return CADDYFILE.read_text(encoding="utf-8")


def _site_block() -> str:
    """Body of the `{$CADDY_SITE_ADDRESSES} { ... }` block, by matching braces."""
    text = _text()
    header = re.search(r"\{\$CADDY_SITE_ADDRESSES\}", text)
    assert header, "missing `{$CADDY_SITE_ADDRESSES}` site block in caddy/Caddyfile"
    open_brace = text.find("{", header.end())
    depth = 0
    for i in range(open_brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace + 1 : i]
    raise AssertionError("unbalanced braces in the site block")


def test_site_block_ends_with_a_refusing_fall_through():
    """A bare `handle` must come last, so unrouted hostnames are refused.

    Last specifically: Caddy runs the first matching `handle`, so a fall-through
    placed above a host block would swallow that hostname's traffic.
    """
    site = _site_block()
    matches = list(re.finditer(r"^\thandle\s*\{", site, re.MULTILINE))
    assert matches, (
        "no bare `handle { ... }` fall-through in the site block — a hostname "
        "with no route would fall out and get Caddy's implicit empty 200"
    )
    assert len(matches) == 1, f"expected one fall-through, found {len(matches)}"

    after = site[matches[0].end() :]
    assert not re.search(r"^\thandle\s+@", after, re.MULTILINE), (
        "a per-host `handle @...` is declared after the fall-through; Caddy takes "
        "the first match, so that hostname would be refused instead of routed"
    )

    body = site[matches[0].end() : matches[0].end() + 200]
    assert re.search(r"respond\s+.*\b404\b", body), (
        f"the fall-through must refuse with 404, found: {body.splitlines()[0]!r}"
    )


def test_plain_http_rejects_unmatched_hostnames():
    """A `:80` site block, for names matching no site address at all.

    HTTPS refuses these at the handshake; plain HTTP would otherwise answer the
    same empty 200.
    """
    text = _text()
    block = re.search(r"^:80\s*\{(.*?)^\}", text, re.MULTILINE | re.DOTALL)
    assert block, "no `:80 { ... }` block — unmatched hostnames over plain HTTP get an empty 200"
    assert re.search(r"respond\s+.*\b404\b", block.group(1)), (
        "the :80 block must refuse with 404"
    )
