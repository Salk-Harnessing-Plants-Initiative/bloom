"""core section — cross-cutting discovery tools, not ``sleap-roots-analyze`` wrappers.

``list_available_experiments``, ``load_experiment_data``, and
``list_existing_analyses`` are thin shims over ``experiment_utils`` / the
injected ports — distinct from the ``sleap_roots`` section, and always
included in the agent's tool set regardless of namespacing (see
``ALWAYS_INCLUDE_MCP_TOOLS`` in ``langchain/helpers/foundational_tools.py``,
matched prefix-aware so the ``core_`` namespace doesn't silently drop them).
The web client marks these the same way, via the ``foundational`` field
``GET /langchain/mcp-tools`` computes from that same set — it no longer
keeps its own copy of these names.

The server mounts this section into the combined ``/mcp`` surface (tools appear
namespaced ``core_<name>``) and serves it at its own ``/core/mcp`` URL.
"""

from fastmcp import FastMCP

from bloom_mcp.auth import auth_provider
from bloom_mcp.contract import register

from . import list_available_experiments, list_existing_analyses, load_experiment_data

section = FastMCP("core", auth=auth_provider)

# Register every tool in this section. Add new tools here.
register(
    section,
    list_available_experiments.list_available_experiments,
    load_experiment_data.load_experiment_data,
    list_existing_analyses.list_existing_analyses,
)
