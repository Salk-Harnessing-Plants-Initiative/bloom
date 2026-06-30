"""
Bloom MCP Server - Exposes SLEAP analysis tools via Model Context Protocol.

Transport: streamable-http on port 8811.

Surfaces:
  - Combined surface at /mcp — every tool, including each section's tools
    (namespaced). This is the endpoint the agent uses; unchanged.
  - One path per section (e.g. /phenotyping_segmentation/mcp) so a Claude
    Desktop client can load just that section. See bloom_mcp/sections/.

Workflow tools (one MCP call runs the full analysis):
  - run_qc_workflow
  - run_outlier_workflow
  - run_descriptive_stats_workflow
  - run_dimensionality_reduction_workflow
  - run_clustering_workflow

Discovery tools (always-on):
  - list_available_experiments
  - load_experiment_data
  - inspect_data_quality
  - list_existing_analyses

Direct tools (granular, available for ad-hoc use):
  - correlation_tools: 8 cross-experiment correlation tools
  - viz_tools:         7 plotting tools

Sections (per-package sub-servers, see bloom_mcp/sections/):
  - phenotyping_segmentation: Lin's segmentation tools (empty scaffold today)
"""

import logging

from fastmcp import FastMCP
from fastmcp.utilities.lifespan import combine_lifespans
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Mount

# Env validation is lazy (see supabase_client / experiment_utils validate_env):
# importing this module no longer requires Supabase or the BLOOM_*_DIR env, so
# `import bloom_mcp` and the unit tests run with no env. main() calls both
# validators at startup to preserve fail-fast-at-boot for a misconfigured deploy.
from bloom_mcp.supabase_client import validate_env as validate_supabase_env
from bloom_mcp.experiment_utils import validate_env as validate_data_env

from bloom_mcp.auth import API_KEY, auth_provider

from bloom_mcp.tools import (
    qc_tools,
    viz_tools,
    correlation_tools,
    storage_tools,
)
from bloom_mcp.tools.workflows import (
    clustering as clustering_workflow,
    dimred as dimred_workflow,
    outlier as outlier_workflow,
    qc as qc_workflow,
    stats as stats_workflow,
)
from bloom_mcp.sections import SECTIONS

logger = logging.getLogger(__name__)

# --- MCP Server (combined surface) ---

mcp = FastMCP("bloom-tools", auth=auth_provider)

# --- Register All Tool Modules ---

# Discovery tools (always-on)
qc_tools.register(mcp)
storage_tools.register(mcp)

# Workflow tools
qc_workflow.register(mcp)
outlier_workflow.register(mcp)
stats_workflow.register(mcp)
dimred_workflow.register(mcp)
clustering_workflow.register(mcp)

# Direct tools (granular)
correlation_tools.register(mcp)
viz_tools.register(mcp)

# --- Sections ---
# Mount each section into the combined server so its tools appear on /mcp,
# namespaced as <section>_<tool>, for the agent. Each section is also served
# at its own URL in build_app() for single-section Claude clients.
for _name, _section in SECTIONS.items():
    mcp.mount(_section, namespace=_name)


# --- Health Endpoint ---
# GET for Docker healthchecks. Bypasses MCP's SSE/JSON-RPC protocol so probes
# don't need an API key, custom Accept header, or POST body. Served at /health
# because the combined app is mounted at the app root (see build_app).
@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


def build_app() -> Starlette:
    """Compose the combined surface and one path per section into one ASGI app.

    The combined surface (all tools + /health) stays at the app root, so /mcp
    and /health are unchanged for the agent and the Docker healthcheck. Each
    section is mounted at /<section> (e.g. /phenotyping_segmentation/mcp). All
    sub-app lifespans are combined so every streamable-http session manager
    starts.
    """
    combined_app = mcp.http_app(path="/mcp")
    section_apps = {
        name: section.http_app(path="/mcp") for name, section in SECTIONS.items()
    }

    # Section paths first (more specific); combined at root last so /mcp and
    # /health fall through to it.
    routes = [Mount(f"/{name}", app=app) for name, app in section_apps.items()]
    routes.append(Mount("/", app=combined_app))

    lifespans = [combined_app.lifespan, *(a.lifespan for a in section_apps.values())]
    return Starlette(routes=routes, lifespan=combine_lifespans(*lifespans))


# --- Entry Point ---


def main() -> None:
    """Validate env, inject persistence adapters, then serve the ASGI app.

    The validators run before the server binds the port so a misconfigured
    deploy fails fast at container boot — preserving the fail-fast that used to
    come from importing ``supabase_client`` / ``experiment_utils``.
    """
    validate_supabase_env()
    validate_data_env()

    # Composition root: inject the production persistence adapters into the
    # tools layer. Tools depend on the ports (bloom_mcp.tools._ports), never on
    # Supabase / AnalysisWriter directly, so swapping a backend is a change here.
    # BucketInputReader is an additive fallback so experiments in bloommcp_input/
    # are readable; it wraps SupabaseReader and changes nothing existing. Remove
    # it once the input migration (#307) folds bucket input into the reader.
    from bloom_mcp.data_access import SupabaseReader
    from bloom_mcp.data_access.bucket_input_reader import BucketInputReader
    from bloom_mcp.result_store import SupabaseResultStore
    from bloom_mcp.tools import _ports

    _ports.configure(
        reader=BucketInputReader(SupabaseReader()),
        store=SupabaseResultStore(),
    )

    if API_KEY:
        print("Bloom MCP Server starting with API key authentication")
    else:
        print("Bloom MCP Server starting without authentication (dev mode)")

    import uvicorn

    uvicorn.run(build_app(), host="0.0.0.0", port=8811)


if __name__ == "__main__":
    main()
