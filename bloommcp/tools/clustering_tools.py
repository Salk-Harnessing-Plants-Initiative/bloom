"""Empty shell — clustering now lives in workflows.clustering.run_clustering_workflow.

The 4 clustering primitives (run_kmeans_clustering, run_gmm_clustering,
run_hierarchical_clustering, get_cluster_quality) collapse into one workflow
with an `algorithm` parameter. Kept temporarily so server.py's
`clustering_tools.register(mcp)` call still resolves; deleted entirely in
the registration cleanup phase.
"""


def register(mcp):
    """No-op — clustering moved to workflows.clustering."""
    return
