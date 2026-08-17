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

``get_download_links`` (bloom#599) is a fourth core tool, also a thin shim
over the injected ``ResultStore`` port — but, unlike the three above,
deliberately **not** foundational: it is a targeted, on-demand retrieval
tool a caller uses once it already has a specific
``(experiment, tool_class, run_ref)`` in hand, discovered dynamically like
the analysis tools rather than always-included (see
``openspec/changes/add-bloommcp-get-download-links/design.md`` Decision 5).

``list_experiment_sources`` (bloom#626) is a fifth core tool, a thin
isinstance-gated shim over ``SourceSelectable.list_sources()`` — also
deliberately not foundational: an occasional discovery aid a caller reaches
for after ``qc_clean``/``qc_inspect``/``load_experiment_data`` flags more
than one raw source, not part of every session's default toolset.

The server mounts this section into the combined ``/mcp`` surface (tools appear
namespaced ``core_<name>``) and serves it at its own ``/core/mcp`` URL.
"""

from fastmcp import FastMCP

from bloom_mcp.auth import auth_provider
from bloom_mcp.contract import register

from . import (
    get_download_links,
    list_available_experiments,
    list_existing_analyses,
    list_experiment_sources,
    load_experiment_data,
)

section = FastMCP("core", auth=auth_provider)

# Register every tool in this section. Add new tools here.
register(
    section,
    list_available_experiments.list_available_experiments,
    load_experiment_data.load_experiment_data,
    list_existing_analyses.list_existing_analyses,
    get_download_links.get_download_links,
    list_experiment_sources.list_experiment_sources,
)
