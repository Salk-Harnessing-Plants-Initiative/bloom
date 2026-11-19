"""core section — cross-cutting discovery tools, not ``sleap-roots-analyze`` wrappers.

``list_available_experiments``, ``load_experiment_data``, and
``list_existing_analyses`` are thin shims over ``experiment_utils`` / the
injected ports — distinct from the ``sleap_roots`` section.

``get_download_links`` (bloom#599) is a fourth core tool, also a thin shim
over the injected ``ResultStore`` port: a targeted, on-demand retrieval tool
a caller uses once it already has a specific
``(experiment, tool_class, run_ref)`` in hand.

``list_experiment_sources`` (bloom#626) is a fifth core tool, a thin
isinstance-gated shim over ``SourceSelectable.list_sources()``: an
occasional discovery aid a caller reaches for after
``qc_clean``/``qc_inspect``/``load_experiment_data`` flags more than one raw
source.

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
