"""Bloom MCP server — SLEAP root-trait analysis tools over the Model Context Protocol.

Importing this package has no side effects and requires no Supabase env; the
Supabase credentials are validated lazily at first access and explicitly at
server startup (see :func:`bloom_mcp.supabase_client.validate_env`).
"""

from __future__ import annotations
