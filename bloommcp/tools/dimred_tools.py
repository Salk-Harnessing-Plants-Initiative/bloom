"""Empty shell — dim reduction now lives in workflows.dimred.run_dimensionality_reduction_workflow.

The 4 PCA-related primitives (run_pca, get_pca_feature_contributions,
plot_pca_scree, plot_pca_biplot) collapse into one workflow with a
`method` parameter that also adds UMAP. Kept temporarily so server.py's
`dimred_tools.register(mcp)` call still resolves; deleted entirely in the
registration cleanup phase.
"""


def register(mcp):
    """No-op — dim reduction moved to workflows.dimred."""
    return
