"""
MCP Tool Wrappers for SLEAP Clustering Analysis.

Wraps functions from source/clustering.py. Uses source/experiment_utils.py for
dynamic experiment discovery and column auto-detection.
"""

import numpy as np
import pandas as pd
from pathlib import Path

from source.clustering import (
    perform_kmeans_clustering,
    perform_gmm_clustering,
    perform_hierarchical_clustering,
    cut_dendrogram,
)
from source.experiment_utils import load_experiment_data as _load_data, PLOTS_DIR, PLOTS_URL


# ============================================================================
# Tool 1: K-Means Clustering
# ============================================================================

def run_kmeans_clustering(
    filename: str,
    n_clusters: str = "",
    max_clusters: int = 10,
) -> str:
    """Run K-Means clustering on a SLEAP experiment.

    Partitions samples into k clusters. If k is not specified, automatically
    selects the optimal k using silhouette score (k=2 to max_clusters).
    Reports cluster sizes, quality metrics, and genotype distribution per cluster.

    Args:
        filename: CSV filename from list_available_experiments
        n_clusters: Number of clusters (empty = auto-select via silhouette)
        max_clusters: Maximum k to test for auto-selection (default 10)
    """
    df, trait_cols, config, source = _load_data(filename)
    if df is None:
        return source

    stem = Path(filename).stem

    # Parse n_clusters
    k = None
    if n_clusters.strip():
        try:
            k = int(n_clusters.strip())
            if k < 2:
                return "n_clusters must be >= 2."
        except ValueError:
            return f"Invalid n_clusters: '{n_clusters}'. Must be an integer."

    try:
        result = perform_kmeans_clustering(
            data=df[trait_cols],
            n_clusters=k,
            max_clusters=max_clusters,
            standardize=True,
        )
    except Exception as e:
        return f"K-Means clustering failed: {e}"

    n_k = result["n_clusters"]
    labels = result["cluster_labels"]
    n_samples = len(labels)

    lines = [
        f"K-Means Clustering: {stem} (source: {source})",
        f"  {n_samples} samples, {len(trait_cols)} traits, k={n_k}"
        + (" (auto-selected)" if k is None else ""),
        "",
        "  Quality Metrics:",
        f"    Silhouette Score:       {result['silhouette_score']:.3f}  ([-1,1], higher=better)",
        f"    Davies-Bouldin Score:   {result['davies_bouldin_score']:.3f}  ([0,inf), lower=better)",
        f"    Calinski-Harabasz Score: {result['calinski_harabasz_score']:.1f}  ([0,inf), higher=better)",
        f"    Inertia:                {result['inertia']:.1f}",
        "",
        "  Cluster Sizes:",
    ]

    for i, size in enumerate(result["cluster_sizes"]):
        pct = size / n_samples * 100
        lines.append(f"    Cluster {i}: {size} samples ({pct:.1f}%)")

    # Show genotype distribution per cluster
    genotype_col = config["genotype_col"]
    if genotype_col and genotype_col in df.columns:
        df_clean = df[trait_cols].dropna()
        geno_values = df.loc[df_clean.index, genotype_col].values

        lines.append("\n  Genotype Distribution per Cluster:")
        for i in range(n_k):
            mask = labels == i
            cluster_genos = pd.Series(geno_values[mask]).value_counts()
            top_genos = cluster_genos.head(3)
            geno_str = ", ".join(f"{g}={c}" for g, c in top_genos.items())
            lines.append(f"    Cluster {i}: {geno_str}" + (f" (+{len(cluster_genos) - 3} more)" if len(cluster_genos) > 3 else ""))

    return "\n".join(lines)


# ============================================================================
# Tool 2: GMM Clustering
# ============================================================================

def run_gmm_clustering(
    filename: str,
    n_components: str = "",
    max_components: int = 5,
) -> str:
    """Run Gaussian Mixture Model (GMM) clustering on a SLEAP experiment.

    Models data as a mixture of Gaussian distributions. Provides both hard
    cluster assignments and soft probabilities. If n_components is not specified,
    auto-selects using BIC (Bayesian Information Criterion).

    Args:
        filename: CSV filename from list_available_experiments
        n_components: Number of mixture components (empty = auto-select via BIC)
        max_components: Maximum components to test for auto-selection (default 5)
    """
    df, trait_cols, config, source = _load_data(filename)
    if df is None:
        return source

    stem = Path(filename).stem

    n_comp = None
    if n_components.strip():
        try:
            n_comp = int(n_components.strip())
            if n_comp < 1:
                return "n_components must be >= 1."
        except ValueError:
            return f"Invalid n_components: '{n_components}'. Must be an integer."

    try:
        result = perform_gmm_clustering(
            data=df[trait_cols],
            n_components=n_comp,
            max_components=max_components,
            standardize=True,
        )
    except Exception as e:
        return f"GMM clustering failed: {e}"

    n_k = result["n_components"]
    labels = result["cluster_labels"]
    n_samples = len(labels)

    lines = [
        f"GMM Clustering: {stem} (source: {source})",
        f"  {n_samples} samples, {len(trait_cols)} traits, k={n_k}"
        + (" (BIC auto-selected)" if n_comp is None else ""),
        f"  Covariance type: {result['covariance_type']}",
        f"  Converged: {result['converged']} ({result['n_iter']} iterations)",
        "",
        "  Model Selection:",
        f"    BIC: {result['bic']:.1f}",
        f"    AIC: {result['aic']:.1f}",
        "",
        "  Quality Metrics:",
        f"    Silhouette Score:       {result['silhouette_score']:.3f}",
        f"    Davies-Bouldin Score:   {result['davies_bouldin_score']:.3f}",
        f"    Calinski-Harabasz Score: {result['calinski_harabasz_score']:.1f}",
        "",
        "  Cluster Sizes (hard assignment):",
    ]

    for i, size in enumerate(result["cluster_sizes"]):
        pct = size / n_samples * 100
        weight = result["weights"][i]
        lines.append(f"    Component {i}: {size} samples ({pct:.1f}%), weight={weight:.3f}")

    probs = result["probabilities"]
    max_probs = probs.max(axis=1)
    lines.append(f"\n  Assignment Confidence:")
    lines.append(f"    Mean max probability: {max_probs.mean():.3f}")
    lines.append(f"    Min max probability:  {max_probs.min():.3f}")
    uncertain = (max_probs < 0.7).sum()
    lines.append(f"    Uncertain samples (max prob < 0.7): {uncertain}")

    return "\n".join(lines)


# ============================================================================
# Tool 3: Hierarchical Clustering
# ============================================================================

def run_hierarchical_clustering(
    filename: str,
    n_clusters: int = 3,
    linkage_method: str = "ward",
) -> str:
    """Run hierarchical clustering and generate a dendrogram plot.

    Builds a hierarchy of clusters using agglomerative clustering. Saves a
    dendrogram plot to the plots directory and reports cluster assignments
    when cut at the specified number of clusters.

    Args:
        filename: CSV filename from list_available_experiments
        n_clusters: Number of clusters to cut the dendrogram at (default 3)
        linkage_method: Linkage method (ward, complete, average, single). Default: ward.
    """
    df, trait_cols, config, source = _load_data(filename)
    if df is None:
        return source

    stem = Path(filename).stem

    if n_clusters < 2:
        return "n_clusters must be >= 2."

    valid_methods = ["ward", "complete", "average", "single"]
    if linkage_method not in valid_methods:
        return f"Invalid linkage method: '{linkage_method}'. Choose from: {', '.join(valid_methods)}"

    try:
        hier_result = perform_hierarchical_clustering(
            data=df[trait_cols],
            method=linkage_method,
            standardize=True,
        )
    except Exception as e:
        return f"Hierarchical clustering failed: {e}"

    try:
        cut_result = cut_dendrogram(hier_result, n_clusters=n_clusters)
    except Exception as e:
        return f"Failed to cut dendrogram: {e}"

    labels = cut_result["cluster_labels"]
    n_samples = len(labels)

    # Generate dendrogram plot
    from scipy.cluster.hierarchy import dendrogram
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(14, 6))
    dendrogram(
        hier_result["linkage_matrix"],
        ax=ax,
        truncate_mode="lastp",
        p=30,
        leaf_rotation=90,
        leaf_font_size=8,
        color_threshold=cut_result["cut_height"],
    )
    ax.set_title(f"Dendrogram: {stem} ({linkage_method} linkage)")
    ax.set_xlabel("Sample (or cluster size)")
    ax.set_ylabel("Distance")
    ax.axhline(y=cut_result["cut_height"], color="red", linestyle="--", alpha=0.7, label=f"Cut at k={n_clusters}")
    ax.legend()

    plot_name = f"dendrogram_{stem}_{linkage_method}.png"
    plot_path = PLOTS_DIR / plot_name
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(plot_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    lines = [
        f"Hierarchical Clustering: {stem} (source: {source})",
        f"  {n_samples} samples, {len(trait_cols)} traits",
        f"  Linkage: {linkage_method}, cut at k={n_clusters}",
        f"  Cophenetic correlation: {hier_result['cophenetic_correlation']:.3f}  ([0,1], higher=better)",
        f"  Cut height: {cut_result['cut_height']:.2f}",
        "",
        "  Quality Metrics:",
        f"    Silhouette Score:       {cut_result['silhouette_score']:.3f}",
        f"    Davies-Bouldin Score:   {cut_result['davies_bouldin_score']:.3f}",
        f"    Calinski-Harabasz Score: {cut_result['calinski_harabasz_score']:.1f}",
        "",
        "  Cluster Sizes:",
    ]

    for i, size in enumerate(cut_result["cluster_sizes"]):
        pct = size / n_samples * 100
        lines.append(f"    Cluster {i}: {size} samples ({pct:.1f}%)")

    lines.append(f"\n  Dendrogram saved: {PLOTS_URL}/{plot_name}")

    return "\n".join(lines)


# ============================================================================
# Tool 4: Cluster Quality Metrics
# ============================================================================

def get_cluster_quality(
    filename: str,
    method: str = "kmeans",
    n_clusters: str = "",
    max_clusters: int = 10,
) -> str:
    """Report clustering quality metrics across different values of k.

    Tests k from 2 to max_clusters and reports silhouette, Davies-Bouldin, and
    Calinski-Harabasz scores for each. Identifies the optimal k for each metric.

    Args:
        filename: CSV filename from list_available_experiments
        method: Clustering method (kmeans or hierarchical). Default: kmeans.
        n_clusters: Specific k to evaluate (empty = test range 2 to max_clusters)
        max_clusters: Maximum k to test (default 10)
    """
    df, trait_cols, config, source = _load_data(filename)
    if df is None:
        return source

    stem = Path(filename).stem

    valid_methods = ["kmeans", "hierarchical"]
    if method not in valid_methods:
        return f"Invalid method: '{method}'. Choose from: {', '.join(valid_methods)}"

    if n_clusters.strip():
        try:
            k = int(n_clusters.strip())
        except ValueError:
            return f"Invalid n_clusters: '{n_clusters}'. Must be an integer."

        try:
            if method == "kmeans":
                result = perform_kmeans_clustering(
                    data=df[trait_cols], n_clusters=k, standardize=True,
                )
            else:
                hier = perform_hierarchical_clustering(
                    data=df[trait_cols], standardize=True,
                )
                result = cut_dendrogram(hier, n_clusters=k)
        except Exception as e:
            return f"Clustering failed: {e}"

        return (
            f"Cluster Quality ({method}, k={k}): {stem} (source: {source})\n"
            f"  Silhouette:       {result['silhouette_score']:.3f}  ([-1,1], higher=better)\n"
            f"  Davies-Bouldin:   {result['davies_bouldin_score']:.3f}  ([0,inf), lower=better)\n"
            f"  Calinski-Harabasz: {result['calinski_harabasz_score']:.1f}  ([0,inf), higher=better)"
        )

    lines = [
        f"Cluster Quality Scan ({method}): {stem} (source: {source})",
        f"  Testing k=2 to k={max_clusters}\n",
        f"  {'k':>3s}  {'Silhouette':>11s}  {'Davies-Bouldin':>14s}  {'Calinski-Harabasz':>17s}",
        f"  {'---' * 17}",
    ]

    silhouette_scores = []
    db_scores = []
    ch_scores = []
    k_values = []

    hier_result = None
    if method == "hierarchical":
        try:
            hier_result = perform_hierarchical_clustering(
                data=df[trait_cols], standardize=True,
            )
        except Exception as e:
            return f"Hierarchical clustering failed: {e}"

    for k in range(2, max_clusters + 1):
        try:
            if method == "kmeans":
                result = perform_kmeans_clustering(
                    data=df[trait_cols], n_clusters=k, standardize=True,
                )
            else:
                result = cut_dendrogram(hier_result, n_clusters=k)

            sil = result["silhouette_score"]
            db = result["davies_bouldin_score"]
            ch = result["calinski_harabasz_score"]

            silhouette_scores.append(sil)
            db_scores.append(db)
            ch_scores.append(ch)
            k_values.append(k)

            lines.append(f"  {k:>3d}  {sil:>11.3f}  {db:>14.3f}  {ch:>17.1f}")
        except Exception:
            lines.append(f"  {k:>3d}  {'failed':>11s}  {'failed':>14s}  {'failed':>17s}")

    if k_values:
        best_sil_k = k_values[np.argmax(silhouette_scores)]
        best_db_k = k_values[np.argmin(db_scores)]
        best_ch_k = k_values[np.argmax(ch_scores)]

        lines.append(f"\n  Optimal k by metric:")
        lines.append(f"    Silhouette:       k={best_sil_k} (score={max(silhouette_scores):.3f})")
        lines.append(f"    Davies-Bouldin:   k={best_db_k} (score={min(db_scores):.3f})")
        lines.append(f"    Calinski-Harabasz: k={best_ch_k} (score={max(ch_scores):.1f})")

    return "\n".join(lines)


# ============================================================================
# Registration
# ============================================================================

def register(mcp):
    """Register all clustering tools with the MCP server."""
    mcp.tool()(run_kmeans_clustering)
    mcp.tool()(run_gmm_clustering)
    mcp.tool()(run_hierarchical_clustering)
    mcp.tool()(get_cluster_quality)
