"""Scaffold tests for the per-package section sub-servers.

Structural only — no running server. These assert the section registry, that
the combined server mounts each section, and that ``build_app()`` composes the
combined surface plus one path per section into a single ASGI app. Running
with Supabase env unset (see conftest), like the rest of the package tests.
"""

from __future__ import annotations

from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Mount

from bloom_mcp import server
from bloom_mcp.sections import SECTIONS


def test_phenotyping_segmentation_section_registered():
    """The segmentation section is in the registry and is a FastMCP server."""
    assert "phenotyping_segmentation" in SECTIONS
    assert isinstance(SECTIONS["phenotyping_segmentation"], FastMCP)


def test_section_name_matches_url_path_and_prefix():
    """A section's registry key is the single source for its URL + tool prefix."""
    for name in SECTIONS:
        # lower_snake_case so it is a valid URL path segment and tool prefix
        assert name == name.lower()
        assert " " not in name


def test_build_app_returns_asgi_app_with_one_mount_per_section():
    """build_app() yields a Starlette app: a mount per section + the root mount."""
    app = server.build_app()
    assert isinstance(app, Starlette)

    mount_paths = {r.path for r in app.routes if isinstance(r, Mount)}
    for name in SECTIONS:
        assert f"/{name}" in mount_paths
    # Combined surface (carrying /mcp and /health) is mounted at the root,
    # which Starlette normalizes to "".
    assert "" in mount_paths


def test_combined_server_exposes_section_tools_namespaced():
    """Section tools reach the agent: they appear on the combined server,
    prefixed by the section name. Skips cleanly while a section has no tools
    (the scaffold state) so this becomes a real assertion once tools land."""
    import asyncio

    tools = {t.name for t in asyncio.run(server.mcp.list_tools())}
    for name, section in SECTIONS.items():
        section_tools = asyncio.run(section.list_tools())
        for tool in section_tools:
            assert f"{name}_{tool.name}" in tools
