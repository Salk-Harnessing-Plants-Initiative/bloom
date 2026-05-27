"""Empty shell — descriptive stats now live in workflows.stats.run_descriptive_stats_workflow.

The original 5 MCP tools here (descriptive stats, ANOVA, heritability,
high-heritability filter, low-heritability diagnostic) collapse into 3
later workflows:
  - descriptive stats → workflows.stats.run_descriptive_stats_workflow (this phase)
  - heritability + diagnostics → workflows.heritability.run_heritability_workflow (Phase 3a)
  - ANOVA is not in the v1 spec — removed
File kept temporarily so server.py's `stats_tools.register(mcp)` still resolves.
Will be deleted entirely in the registration cleanup phase.
"""


def register(mcp):
    """No-op — descriptive stats moved to workflows.stats."""
    return
