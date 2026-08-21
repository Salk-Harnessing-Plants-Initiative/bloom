"""Config-shape test: hostnames with no route must be refused, not answered.

`CADDY_SITE_ADDRESSES` carries a wildcard, so the certificate covers every
one-level subdomain and Caddy accepts the connection for any of them. A name
with no `handle @host` block falls out of the site block, and Caddy's implicit
response to that is an empty `200 OK` — a success code for a page we do not
serve. Anything checking only the status reads it as healthy, which is how the
old status-only Studio reachability test came to pass against nothing — it was
since replaced by `test_studio_requires_credentials` and
`test_studio_reachable_with_credentials`, which assert the gate and a non-empty
body (#689, #649).

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


def _mask_quoted(text: str) -> str:
    """Blank out quoted spans, preserving length so indices still line up.

    Brace math runs over the masked copy: a `{` inside a header value is data,
    not a block delimiter. Only `"` and backtick delimit a Caddy token — an
    apostrophe is literal, and quote state resets at each newline so an
    unbalanced quote costs one line rather than the rest of the file.
    """
    out = list(text)
    quote = None
    for i, char in enumerate(text):
        if char == "\n":
            quote = None
        elif quote:
            out[i] = " "
            if char == quote:
                quote = None
        elif char in '"`':
            quote = char
            out[i] = " "
    return "".join(out)


def _strip_comments(text: str) -> str:
    """Drop `#` comments, trailing as well as whole-line.

    Without this, prose mentioning `{$CADDY_SITE_ADDRESSES}` above the block
    matches first, and a stray `}` inside a comment truncates the slice.
    """
    lines = []
    for line in text.splitlines():
        cut = _mask_quoted(line).find("#")
        lines.append(line if cut == -1 else line[:cut])
    return "\n".join(lines)


def _site_block() -> str:
    """Body of the `{$CADDY_SITE_ADDRESSES} { ... }` block, by matching braces."""
    text = _strip_comments(_text())
    masked = _mask_quoted(text)
    header = re.search(r"\{\$CADDY_SITE_ADDRESSES\}", masked)
    assert header, "missing `{$CADDY_SITE_ADDRESSES}` site block in caddy/Caddyfile"
    open_brace = masked.find("{", header.end())
    depth = 0
    for i in range(open_brace, len(masked)):
        if masked[i] == "{":
            depth += 1
        elif masked[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace + 1 : i]
    raise AssertionError("unbalanced braces in the site block")


def test_site_block_has_a_refusing_fall_through():
    """A bare `handle` in the site block, refusing hostnames with no route.

    Position in the file does not matter: Caddy's adapter sorts a matcher-less
    `handle` after every matched one regardless of where it is written, so this
    asserts existence and value, not placement. Keeping it last is a readability
    convention, not a correctness requirement.
    """
    site = _site_block()
    matches = list(re.finditer(r"^\thandle\s*\{", site, re.MULTILINE))
    assert matches, (
        "no bare `handle { ... }` fall-through in the site block — a hostname "
        "with no route would fall out and get Caddy's implicit empty 200"
    )
    assert len(matches) == 1, f"expected one fall-through, found {len(matches)}"

    masked = _mask_quoted(site)
    depth, body_start = 1, matches[0].end()
    for i in range(body_start, len(masked)):
        depth += (masked[i] == "{") - (masked[i] == "}")
        if depth == 0:
            body = site[body_start:i]
            break
    else:
        raise AssertionError("unbalanced braces in the fall-through block")
    assert re.search(r"^\s*respond\s+.+\s+404\s*$", body, re.MULTILINE), (
        f"the fall-through must refuse with a 404 and a reason phrase, found: {body.strip()!r}"
    )


def test_plain_http_rejects_unmatched_hostnames():
    """A `:80` site block, for names matching no site address at all.

    HTTPS refuses these at the handshake; plain HTTP would otherwise answer the
    same empty 200.
    """
    text = _strip_comments(_text())
    block = re.search(r"^:80\s*\{(.*?)^\}", text, re.MULTILINE | re.DOTALL)
    assert block, "no `:80 { ... }` block — unmatched hostnames over plain HTTP get an empty 200"
    # Anchored: a bare `respond <body> 404` and nothing else. An unanchored search
    # accepts `respond "404 in the text" 200`, and a path token would scope the
    # refusal to one path while everything else still gets the empty 200.
    assert re.search(r"^\s*respond\s+.+\s+404\s*$", block.group(1), re.MULTILINE), (
        f"the :80 block must refuse every path with a 404, found: {block.group(1).strip()!r}"
    )


def test_https_rejects_hostnames_matching_no_site_address():
    """A `:443` block, mirroring the `:80` one.

    A Host outside every site address never enters the site block, so the
    fall-through cannot catch it — it falls out of the server and Caddy answers
    with an empty 200. Reaching this over HTTPS needs a certificate-covered SNI,
    but Host is independent of SNI, so presenting a name we serve and then asking
    for anything else gets there.
    """
    text = _strip_comments(_text())
    block = re.search(r"^:443\s*\{(.*?)^\}", text, re.MULTILINE | re.DOTALL)
    assert block, (
        "no `:443 { ... }` block — a Host outside every site address would fall "
        "out of the server and get Caddy's implicit empty 200"
    )
    assert re.search(r"^\s*respond\s+.+\s+404\s*$", block.group(1), re.MULTILINE), (
        f"the :443 block must refuse every path with a 404, found: {block.group(1).strip()!r}"
    )
