"""
Bloom MCP Server - Exposes SLEAP analysis tools via Model Context Protocol.

Transport: streamable-http on port 8811

Tool modules (39 tools total):
  - qc_tools:          6 tools  (experiment discovery, data quality, cleanup)
  - stats_tools:       5 tools  (descriptive stats, ANOVA, heritability)
  - dimred_tools:      4 tools  (PCA analysis, feature contributions, plots)
  - clustering_tools:  4 tools  (K-Means, GMM, hierarchical, quality metrics)
  - outlier_tools:     5 tools  (Mahalanobis, Isolation Forest, PCA, consensus)
  - viz_tools:         7 tools  (histograms, boxplots, heatmaps, dendrograms)
  - correlation_tools: 8 tools  (cross-experiment correlations, power analysis)
"""
import hmac
import os

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from tools import (
    qc_tools,
    stats_tools,
    dimred_tools,
    clustering_tools,
    outlier_tools,
    viz_tools,
    correlation_tools,
)

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

qc_tools.register(mcp)
stats_tools.register(mcp)
dimred_tools.register(mcp)
clustering_tools.register(mcp)
outlier_tools.register(mcp)
viz_tools.register(mcp)
correlation_tools.register(mcp)

# --- Health Endpoint ---
# GET for Docker healthchecks. Bypasses MCP's SSE/JSON-RPC
# protocol so probes don't need an API key, custom Accept header, or POST body.
@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")

# --- Entry Point ---

if __name__ == "__main__":
    if API_KEY:
        print("Bloom MCP Server starting with API key authentication")
    else:
        print("Bloom MCP Server starting without authentication (dev mode)")
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8811)
