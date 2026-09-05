"""Bloom MCP server — SLEAP root-trait analysis tools over the Model Context Protocol.

Importing this package has no side effects and requires no Supabase env; the
Supabase credentials are validated lazily at first access and explicitly at
server startup (see :func:`bloom_mcp.supabase_client.validate_env`).
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    # Single source of truth: the version comes from the installed package
    # metadata (built from pyproject.toml), so a version bump can't drift from
    # what `bloom-mcp --version` prints. Same pattern as bloomctl.__version__.
    __version__ = version("bloommcp")
except PackageNotFoundError:  # running from a source tree that isn't installed
    __version__ = "0.0.0+unknown"
