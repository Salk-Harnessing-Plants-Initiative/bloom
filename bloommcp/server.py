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
from fastmcp import FastMCP

from tools import (
    qc_tools,
    stats_tools,
    dimred_tools,
    clustering_tools,
    outlier_tools,
    viz_tools,
    correlation_tools,
)

# --- MCP Server ---

mcp = FastMCP("bloom-tools")

# --- Register All Tool Modules ---

qc_tools.register(mcp)
stats_tools.register(mcp)
dimred_tools.register(mcp)
clustering_tools.register(mcp)
outlier_tools.register(mcp)
viz_tools.register(mcp)
correlation_tools.register(mcp)


# --- Entry Point ---

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8811)
