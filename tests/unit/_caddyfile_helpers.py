"""Shared Caddyfile parsing for the config-shape tests.

`test_caddy_client_info_route.py`, `test_caddy_cyl_video_route.py` and
`test_caddy_security_headers.py` all slice `caddy/Caddyfile` by matching braces
rather than substring search, so a directive cannot false-pass by living in a
block that does not serve the traffic under test. That parser lived in three
verbatim copies; it lives here now, so a fix lands once instead of drifting.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CADDYFILE = REPO_ROOT / "caddy" / "Caddyfile"


def text() -> str:
    return CADDYFILE.read_text(encoding="utf-8")


def mask_quoted(text: str) -> str:
    """Blank out quoted spans, preserving length so indices still line up.

    Brace math runs over the masked copy: a `{` inside a header value is data,
    not a block delimiter, and counting it desynchronises every depth
    calculation downstream.

    Only `"` and backtick delimit a Caddy token — an apostrophe is literal, and
    treating it as a quote would swallow the rest of the file the first time a
    comment says "Caddy's". Quote state resets at each newline for the same
    reason: an unbalanced quote then costs one line, not everything after it.
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
        elif char in "\"`":
            quote = char
            out[i] = " "
    return "".join(out)


def strip_comments(text: str) -> str:
    """Drop `#` comments — trailing as well as whole-line — so prose mentioning
    a directive can't satisfy an assertion about the directive itself.

    Quote-aware: a `#` inside a quoted value is part of the value. Stripping
    only whole-line comments would both reject a legal trailing comment and, if
    a comment happened to contain a brace, desynchronise the depth math.
    """
    lines = []
    for line in text.splitlines():
        cut = mask_quoted(line).find("#")
        lines.append(line if cut == -1 else line[:cut])
    return "\n".join(lines)


def block_after(text: str, header_pattern: str) -> str | None:
    """Body of the first `<header> {` directive, sliced by matching braces."""
    masked = mask_quoted(text)
    header = re.search(header_pattern, masked)
    if not header:
        return None
    open_brace = masked.find("{", header.end())
    if open_brace == -1:
        return None
    depth = 0
    for i in range(open_brace, len(masked)):
        if masked[i] == "{":
            depth += 1
        elif masked[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace + 1 : i]
    return None


def main_block(text: str) -> str | None:
    """The body of the `handle @main { ... }` host block."""
    return block_after(text, r"handle\s+@main\b")


def site_block(text: str) -> str | None:
    """Body of the `{$CADDY_SITE_ADDRESSES} { ... }` site block."""
    return block_after(text, r"\{\$CADDY_SITE_ADDRESSES\}")
