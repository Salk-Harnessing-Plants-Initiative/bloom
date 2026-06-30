"""phenotyping_segmentation section — Lin's segmentation / phenotyping tools.

This section is a folder: **one file per tool**. Each tool file defines its
input/output models and an ``@as_mcp_tool`` function; this ``__init__`` creates
the section server and registers each tool. Adding a tool = new file in this
folder + one line in the ``register(...)`` call below.

The server mounts this section into the combined ``/mcp`` surface (tools appear
namespaced ``phenotyping_segmentation_<name>``) and serves it at its own
``/phenotyping_segmentation/mcp`` URL — no ``server.py`` edit needed.

Every tool goes through the same shared contract (``@as_mcp_tool``) as every
other bloommcp tool: the section boundary is organizational, not a second
quality bar. Guide: ``_WIKI/BLOOMMCP/adding-a-section-tool.md``.
"""

from fastmcp import FastMCP

from bloom_mcp.auth import auth_provider
from bloom_mcp.contract import register

from . import summarize_trait

section = FastMCP("phenotyping-segmentation", auth=auth_provider)

# Register every tool in this section. Add new tools here.
register(section, summarize_trait.summarize_trait)
