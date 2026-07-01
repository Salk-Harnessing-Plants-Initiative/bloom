"""
Bloom MCP Server - Exposes SLEAP analysis tools via Model Context Protocol.

Transport: streamable-http on port 8811

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
  - qc_clean:          clean a raw trait table for analysis (delegates to
                       sleap_roots_analyze.clean_traits_for_analysis)
  - qc_inspect:        read-only NaN/missingness report + threshold recommendation at
                       QC time (delegates to sleap_roots_analyze EDA functions)
  - correlation_tools: 8 cross-experiment correlation tools
  - viz_tools:         7 plotting tools
"""

import hmac
import logging
import os

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import PlainTextResponse

# Env validation is lazy (see supabase_client / experiment_utils validate_env):
# importing this module no longer requires Supabase or the BLOOM_*_DIR env, so
# `import bloom_mcp` and the unit tests run with no env. main() calls both
# validators at startup to preserve fail-fast-at-boot for a misconfigured deploy.
from bloom_mcp.supabase_client import validate_env as validate_supabase_env
from bloom_mcp.experiment_utils import validate_env as validate_data_env

from bloom_mcp.tools import (
    qc_tools,
    viz_tools,
    correlation_tools,
    storage_tools,
    qc_clean_tool,
    qc_inspect_tool,
)
from bloom_mcp.tools.workflows import (
    clustering as clustering_workflow,
    dimred as dimred_workflow,
    outlier as outlier_workflow,
    qc as qc_workflow,
    stats as stats_workflow,
)

logger = logging.getLogger(__name__)

# --- Authentication ---

API_KEY = os.getenv("BLOOMMCP_API_KEY")

auth_provider = None
if API_KEY:
    from fastmcp.server.auth import TokenVerifier, AccessToken

    class ApiKeyVerifier(TokenVerifier):
        """Validates Bearer token against BLOOMMCP_API_KEY env var."""

        async def verify_token(self, token: str) -> AccessToken | None:
            if hmac.compare_digest(token, API_KEY):
                return AccessToken(
                    token=token, client_id="bloom-client", scopes=["tools"]
                )
            return None

    auth_provider = ApiKeyVerifier()

# --- MCP Server ---

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
qc_clean_tool.register(mcp)
qc_inspect_tool.register(mcp)
correlation_tools.register(mcp)
viz_tools.register(mcp)


# --- Health Endpoint ---
# GET for Docker healthchecks. Bypasses MCP's SSE/JSON-RPC
# protocol so probes don't need an API key, custom Accept header, or POST body.
@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


# --- Entry Point ---


def main() -> None:
    """Validate the Supabase env, then start the MCP server.

    The validators run before ``mcp.run()`` binds the port so a misconfigured
    deploy fails fast at container boot — preserving the fail-fast that used to
    come from importing ``supabase_client`` / ``experiment_utils``.
    """
    validate_supabase_env()
    validate_data_env()

    # Composition root: inject the production persistence adapters into the
    # tools layer. Tools depend on the ports (bloom_mcp.tools._ports), never on
    # Supabase / AnalysisWriter directly, so swapping a backend is a change here.
    from bloom_mcp.data_access import SupabaseReader
    from bloom_mcp.result_store import SupabaseResultStore
    from bloom_mcp.tools import _ports

    _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    if API_KEY:
        print("Bloom MCP Server starting with API key authentication")
    else:
        print("Bloom MCP Server starting without authentication (dev mode)")
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8811)


if __name__ == "__main__":
    main()
