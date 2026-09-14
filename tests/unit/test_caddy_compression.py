"""Config-shape test for response compression in `caddy/Caddyfile`.

The type list is explicit because Caddy's default covers every `text/*` type,
event streams included. The stream routes skip `encode` entirely, since it holds
a response's headers until the first body byte.
"""

from __future__ import annotations

import re

import pytest

from tests.unit._caddyfile_helpers import (
    block_after,
    mask_quoted,
    site_block,
    strip_comments,
    text,
)

EXPECTED_FORMATS = ["zstd", "gzip"]

ENCODE_MATCHER = "@compress"
STREAM_ROUTES = {"/langchain/*", "/bloommcp/*"}

# JSON, HTML, JavaScript, CSS and plain text.
EXPECTED_TYPES = {
    "application/json*",
    "application/javascript*",
    "text/javascript*",
    "text/css*",
    "text/html*",
    "text/plain*",
}

COMPRESSED = [
    "application/json",
    "application/json; charset=utf-8",
    "text/html; charset=utf-8",
    "application/javascript",
    "text/javascript; charset=utf-8",
    "text/css",
    "text/plain; charset=utf-8",
]

NOT_COMPRESSED = [
    "text/event-stream",
    "text/event-stream; charset=utf-8",
    "image/png",
    "image/jpeg",
    "video/mp4",
    "application/octet-stream",
]

_ENCODE_TOKEN = re.compile(r"(?<![\w.-])encode(?![\w.-])")
_CONTENT_TYPE_LINE = re.compile(r"^\s*header\s+Content-Type\s+(\S+)\s*$", re.MULTILINE)


def _encode_directives(body: str) -> list[tuple[int, str, str | None]]:
    """Every `encode` in `body` as `(depth, directive line, block body or None)`."""
    masked = mask_quoted(body)
    found = []
    for match in _ENCODE_TOKEN.finditer(masked):
        start = match.start()
        depth = masked.count("{", 0, start) - masked.count("}", 0, start)
        line = body[start:].splitlines()[0].strip()
        block = block_after(body[start:], r"encode\b") if line.endswith("{") else None
        found.append((depth, line, block))
    return found


def _site() -> str:
    site = site_block(strip_comments(text()))
    assert site is not None, "missing `{$CADDY_SITE_ADDRESSES}` site block in caddy/Caddyfile"
    return site


def _site_encode() -> tuple[str, str | None]:
    top = [(line, block) for depth, line, block in _encode_directives(_site()) if depth == 0]
    assert len(top) == 1, f"expected one site-level `encode`, found {len(top)}"
    return top[0]


def _encode_tokens() -> tuple[str | None, list[str]]:
    """The site-level `encode`'s matcher (or None) and its formats."""
    line, _ = _site_encode()
    tokens = line.rstrip("{").split()[1:]
    if tokens and tokens[0].startswith("@"):
        return tokens[0], tokens[1:]
    return None, tokens


def _matcher_definition(name: str) -> list[list[str]]:
    """Token lists of every site-level definition of the named matcher."""
    site = _site()
    masked = mask_quoted(site)
    found = []
    for match in re.finditer(rf"^[ \t]*{re.escape(name)}[ \t]+(.+)$", site, re.MULTILINE):
        depth = masked.count("{", 0, match.start()) - masked.count("}", 0, match.start())
        if depth == 0:
            found.append(match.group(1).split())
    return found


def _match_types() -> set[str]:
    _, block = _site_encode()
    assert block is not None, "site-level `encode` has no block, so Caddy's default list applies"
    match = block_after(block, r"(?<![\w.-])match\b")
    assert match is not None, "site-level `encode` has no `match` block"
    return set(_CONTENT_TYPE_LINE.findall(match))


def _caddy_header_match(pattern: str, value: str) -> bool:
    """Caddy's header matcher: `*` at either end is a prefix/suffix/substring wildcard."""
    if pattern.startswith("*") and pattern.endswith("*") and len(pattern) > 1:
        return pattern[1:-1] in value
    if pattern.endswith("*"):
        return value.startswith(pattern[:-1])
    if pattern.startswith("*"):
        return value.endswith(pattern[1:])
    return value == pattern


def test_site_block_compresses_with_zstd_then_gzip():
    _, formats = _encode_tokens()
    assert formats == EXPECTED_FORMATS


def test_encode_is_scoped_by_the_compress_matcher():
    matcher, _ = _encode_tokens()
    assert matcher == ENCODE_MATCHER, f"`encode` must be scoped by {ENCODE_MATCHER}, found {matcher}"


def test_the_stream_routes_skip_encode():
    definitions = _matcher_definition(ENCODE_MATCHER)
    assert len(definitions) == 1, f"expected one site-level {ENCODE_MATCHER}, found {len(definitions)}"
    tokens = definitions[0]
    assert tokens[:2] == ["not", "path"], f"{ENCODE_MATCHER} must be `not path ...`, found {tokens}"
    assert set(tokens[2:]) == STREAM_ROUTES


def test_match_list_is_exactly_the_text_types():
    assert _match_types() == EXPECTED_TYPES


@pytest.mark.parametrize("content_type", COMPRESSED)
def test_text_responses_are_compressed(content_type):
    assert any(_caddy_header_match(p, content_type) for p in _match_types())


@pytest.mark.parametrize("content_type", NOT_COMPRESSED)
def test_event_stream_and_media_are_not_compressed(content_type):
    matched = [p for p in _match_types() if _caddy_header_match(p, content_type)]
    assert not matched, f"{content_type} would be compressed by {matched}"


def test_no_encode_below_site_level():
    nested = [line for depth, line, _ in _encode_directives(_site()) if depth > 0]
    assert not nested, f"`encode` inside a route overrides the site list: {nested}"


def test_no_encode_outside_the_site_block():
    everywhere = _encode_directives(strip_comments(text()))
    in_site = _encode_directives(_site())
    assert len(everywhere) == len(in_site), "`encode` found outside the site block"
