"""Per-package MCP sub-servers ("sections").

Each section is its own ``FastMCP`` instance wrapping one package. The server
mounts every section into the combined ``bloom-tools`` surface (so its tools
appear, namespaced, on ``/mcp`` for the agent) and also serves each section at
its own URL (so a Claude Desktop client can load just one section). See
``bloommcp/docs/2026-06-29-bloom-mcp-contributor-namespacing.md``.

To add a section: create ``bloom_mcp/sections/<name>/`` (a folder, one file per
tool) whose ``__init__`` exposes a ``section`` FastMCP instance, then add it to
``SECTIONS`` below. That is the only server-side wiring — no ``server.py`` edit
per tool.
"""

from bloom_mcp.sections import phenotyping_segmentation

# section name (also its URL path + tool-name prefix) -> sub-server
SECTIONS = {
    "phenotyping_segmentation": phenotyping_segmentation.section,
}
