"""Core visualization functions for clustering analysis.

This module provides reusable plotting functions for clustering results that can be
used for both standalone clustering analysis and outlier detection visualizations.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Patch
from scipy.cluster.hierarchy import dendrogram
from sklearn.metrics import silhouette_samples


def create_cluster_scatter_pca(
    cluster_result: Dict,
    pca_result: Optional[Dict] = None,
    highlight_indices: Optional[List] = None,
    figsize: Tuple[int, int] = (10, 8),
    title: str = "Clustering Results (PCA projection)",
) -> plt.Figure:
    """Create PCA scatter plot colored by cluster assignment.

    Uses existing PCA results if provided, otherwise computes PCA for visualization.
    This allows efficient reuse of PCA computed once in notebooks.

    Args:
        cluster_result: Result dictionary from clustering (must contain
            'cluster_labels', 'data_processed', 'data_indices')
        pca_result: Optional PCA results from perform_pca_analysis(). If provided,
            uses these PCA components. If None, computes PCA for visualization.
        highlight_indices: Optional list of sample indices to highlight (e.g., outliers)
        figsize: Figure size (width, height)
        title: Plot title

    Returns:
        matplotlib Figure with PCA scatter plot

    Examples:
        >>> # Option 1: Reuse existing PCA (efficient for notebooks)
        >>> pca_result = perform_pca_analysis(df)
        >>> fig = create_cluster_scatter_pca(kmeans_result, pca_result=pca_result)
        >>>
        >>> # Option 2: Compute PCA for visualization only
        >>> fig = create_cluster_scatter_pca(kmeans_result)
    """
    from .pca import perform_pca_analysis

    fig, ax = plt.subplots(figsize=figsize)

    cluster_labels = cluster_result["cluster_labels"]
    data_indices = cluster_result["data_indices"]

    # Use provided PCA or compute new one
    if pca_result is not None:
        X_pca = pca_result["transformed_data"]
        var_ratios = pca_result["explained_variance_ratio"]
    else:
        # Compute PCA for visualization (2 components)
        X_processed = cluster_result["data_processed"]
        pca_result_new = perform_pca_analysis(
            pd.DataFrame(X_processed),
            n_components=2,
            standardize=False,  # Data already standardized/processed
            random_state=42,
        )
        X_pca = pca_result_new["transformed_data"]
        var_ratios = pca_result_new["explained_variance_ratio"]

    # Ensure we have at least 2 components
    if X_pca.shape[1] < 2:
        ax.text(
            0.5,
            0.5,
            "Need at least 2 PCA components for visualization",
            ha="center",
            va="center",
            transform=ax.transAxes,
        )
        ax.set_title(title)
        return fig

    # Create color map for clusters
    n_clusters = len(np.unique(cluster_labels))
    colors = plt.cm.tab10(np.linspace(0, 1, n_clusters))

    # Plot each cluster
    for i in range(n_clusters):
        cluster_mask = cluster_labels == i
        ax.scatter(
            X_pca[cluster_mask, 0],
            X_pca[cluster_mask, 1],
            c=[colors[i]],
            label=f"Cluster {i + 1}",
            alpha=0.6,
            s=50,
        )

    # Highlight specific indices if provided (e.g., outliers)
    if highlight_indices:
        highlight_mask = np.array([idx in highlight_indices for idx in data_indices])
        if highlight_mask.any():
            ax.scatter(
                X_pca[highlight_mask, 0],
                X_pca[highlight_mask, 1],
                c="red",
                marker="x",
                s=200,
                linewidths=3,
                label="Highlighted",
                zorder=10,
            )

    ax.set_xlabel(f"PC1 ({var_ratios[0]:.1%} variance)")
    ax.set_ylabel(f"PC2 ({var_ratios[1]:.1%} variance)")
    ax.set_title(title)
    ax.legend()
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    return fig


def create_distance_distribution_plot(
    distances: np.ndarray,
    threshold: float,
    method_name: str,
    figsize: Tuple[int, int] = (10, 6),
) -> plt.Figure:
    """Plot distribution of distances/scores with threshold line.

    Used for visualizing distance-based outlier detection (K-Means distances,
    GMM log-likelihoods, etc.).

    Args:
        distances: Array of distances or scores
        threshold: Threshold value for outlier detection
        method_name: Name of the method (for plot title)
        figsize: Figure size (width, height)

    Returns:
        matplotlib Figure with distance distribution
    """
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=figsize)

    # Histogram with threshold line
    ax1.hist(distances, bins=30, alpha=0.7, edgecolor="black", color="skyblue")
    ax1.axvline(
        threshold,
        color="red",
        linestyle="--",
        linewidth=2,
        label=f"Threshold: {threshold:.3f}",
    )
    ax1.set_xlabel("Distance/Score")
    ax1.set_ylabel("Frequency")
    ax1.set_title(f"{method_name} Distance Distribution")
    ax1.legend()
    ax1.grid(True, alpha=0.3)

    # Sorted scatter plot
    sorted_idx = np.argsort(distances)
    sorted_distances = distances[sorted_idx]
    colors = ["red" if d > threshold else "blue" for d in sorted_distances]

    ax2.scatter(
        range(len(sorted_distances)), sorted_distances, c=colors, alpha=0.6, s=30
    )
    ax2.axhline(
        threshold,
        color="red",
        linestyle="--",
        linewidth=2,
        label=f"Threshold: {threshold:.3f}",
    )
    ax2.set_xlabel("Sample Index (sorted by distance)")
    ax2.set_ylabel("Distance/Score")
    ax2.set_title(f"{method_name} Distances (Sorted)")
    ax2.grid(True, alpha=0.3)

    # Add legend for colors
    legend_elements = [
        Patch(facecolor="blue", alpha=0.6, label="Normal"),
        Patch(facecolor="red", alpha=0.6, label="Above Threshold"),
    ]
    ax2.legend(handles=legend_elements)

    plt.tight_layout()
    return fig


def create_cluster_size_barplot(
    cluster_labels: np.ndarray, n_clusters: int, figsize: Tuple[int, int] = (10, 6)
) -> plt.Figure:
    """Create bar plot showing number of samples per cluster.

    Args:
        cluster_labels: Cluster assignment for each sample
        n_clusters: Number of clusters
        figsize: Figure size (width, height)

    Returns:
        matplotlib Figure with cluster size bar plot
    """
    fig, ax = plt.subplots(figsize=figsize)

    # Calculate cluster sizes
    cluster_sizes = [np.sum(cluster_labels == i) for i in range(n_clusters)]

    # Create bar plot
    colors = plt.cm.tab10(np.linspace(0, 1, n_clusters))
    bars = ax.bar(
        range(1, n_clusters + 1),
        cluster_sizes,
        color=colors,
        alpha=0.7,
        edgecolor="black",
    )

    # Add value labels on bars
    for i, bar in enumerate(bars):
        height = bar.get_height()
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            height,
            f"{int(height)}",
            ha="center",
            va="bottom",
            fontweight="bold",
        )

    ax.set_xlabel("Cluster")
    ax.set_ylabel("Number of Samples")
    ax.set_title("Cluster Size Distribution")
    ax.set_xticks(range(1, n_clusters + 1))
    ax.grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    return fig


def create_bic_aic_comparison_plot(
    bic_scores: List[float],
    aic_scores: List[float],
    figsize: Tuple[int, int] = (10, 6),
) -> plt.Figure:
    """Plot BIC/AIC scores for GMM component selection.

    Lower BIC/AIC indicates better model fit with appropriate complexity penalty.

    Args:
        bic_scores: BIC scores for different numbers of components
        aic_scores: AIC scores for different numbers of components
        figsize: Figure size (width, height)

    Returns:
        matplotlib Figure with BIC/AIC comparison
    """
    fig, ax = plt.subplots(figsize=figsize)

    n_components_tested = range(1, len(bic_scores) + 1)

    # Plot both BIC and AIC
    ax.plot(
        n_components_tested,
        bic_scores,
        marker="o",
        linewidth=2,
        markersize=8,
        label="BIC (Bayesian Information Criterion)",
        color="blue",
    )
    ax.plot(
        n_components_tested,
        aic_scores,
        marker="s",
        linewidth=2,
        markersize=8,
        label="AIC (Akaike Information Criterion)",
        color="green",
    )

    # Mark optimal points
    optimal_bic_idx = np.argmin(bic_scores)
    optimal_aic_idx = np.argmin(aic_scores)

    ax.scatter(
        [optimal_bic_idx + 1],
        [bic_scores[optimal_bic_idx]],
        s=200,
        c="blue",
        marker="*",
        zorder=5,
        edgecolors="black",
        linewidths=2,
        label=f"Optimal BIC (k={optimal_bic_idx + 1})",
    )
    ax.scatter(
        [optimal_aic_idx + 1],
        [aic_scores[optimal_aic_idx]],
        s=200,
        c="green",
        marker="*",
        zorder=5,
        edgecolors="black",
        linewidths=2,
        label=f"Optimal AIC (k={optimal_aic_idx + 1})",
    )

    ax.set_xlabel("Number of Components")
    ax.set_ylabel("Information Criterion")
    ax.set_title("GMM Model Selection: BIC vs AIC\n(Lower is better)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    ax.set_xticks(n_components_tested)

    plt.tight_layout()
    return fig


def create_silhouette_plot(
    cluster_result: Dict,
    figsize: Tuple[int, int] = (10, 8),
) -> plt.Figure:
    """Create silhouette plot showing per-sample silhouette scores.

    Silhouette score measures how similar a sample is to its own cluster compared
    to other clusters. Scores range from -1 (wrong cluster) to +1 (perfect cluster).

    Args:
        cluster_result: Result dictionary from clustering (must contain
            'cluster_labels', 'data_processed', and 'silhouette_score')
        figsize: Figure size (width, height)

    Returns:
        matplotlib Figure with silhouette plot
    """
    fig, ax = plt.subplots(figsize=figsize)

    cluster_labels = cluster_result["cluster_labels"]
    X_processed = cluster_result["data_processed"]
    average_score = cluster_result["silhouette_score"]

    # Calculate per-sample silhouette scores
    silhouette_vals = silhouette_samples(X_processed, cluster_labels)

    y_lower = 10
    n_clusters = len(np.unique(cluster_labels))
    colors = plt.cm.tab10(np.linspace(0, 1, n_clusters))

    for i in range(n_clusters):
        # Get silhouette scores for this cluster
        cluster_silhouette_values = silhouette_vals[cluster_labels == i]
        cluster_silhouette_values.sort()

        size_cluster_i = cluster_silhouette_values.shape[0]
        y_upper = y_lower + size_cluster_i

        ax.fill_betweenx(
            np.arange(y_lower, y_upper),
            0,
            cluster_silhouette_values,
            facecolor=colors[i],
            edgecolor=colors[i],
            alpha=0.7,
        )

        # Label the silhouette plots with their cluster numbers at the middle
        ax.text(-0.05, y_lower + 0.5 * size_cluster_i, str(i + 1))

        # Compute the new y_lower for next plot
        y_lower = y_upper + 10

    ax.set_xlabel("Silhouette Coefficient")
    ax.set_ylabel("Cluster")
    ax.set_title(f"Silhouette Plot\nAverage Score: {average_score:.3f}")

    # The vertical line for average silhouette score
    ax.axvline(x=average_score, color="red", linestyle="--", linewidth=2)
    ax.set_yticks([])  # Clear the yaxis labels / ticks
    ax.set_xlim([-0.1, 1])

    plt.tight_layout()
    return fig


def create_dendrogram(
    hierarchical_result: Dict,
    labels: Optional[List[str]] = None,
    cut_height: Optional[float] = None,
    n_clusters: Optional[int] = None,
    color_threshold: Optional[float] = None,
    figsize: Tuple[int, int] = (12, 8),
    title: str = "Hierarchical Clustering Dendrogram",
) -> plt.Figure:
    """Create dendrogram from hierarchical clustering result.

    Args:
        hierarchical_result: Result dictionary from perform_hierarchical_clustering()
            (must contain 'linkage_matrix')
        labels: Optional list of sample labels for dendrogram leaves
        cut_height: Optional height at which to draw horizontal cut line
        n_clusters: Optional number of clusters (calculates cut height automatically)
        color_threshold: Height threshold for coloring clusters (default: no coloring)
        figsize: Figure size (width, height)
        title: Plot title

    Returns:
        matplotlib Figure with dendrogram

    Examples:
        >>> # Basic dendrogram
        >>> fig = create_dendrogram(hier_result)
        >>>
        >>> # With cut line at specific height
        >>> fig = create_dendrogram(hier_result, cut_height=5.0)
        >>>
        >>> # With automatic cut for n clusters
        >>> fig = create_dendrogram(hier_result, n_clusters=3)
        >>>
        >>> # With colored clusters
        >>> fig = create_dendrogram(hier_result, n_clusters=3, color_threshold=5.0)
    """
    fig, ax = plt.subplots(figsize=figsize)

    linkage_matrix = hierarchical_result["linkage_matrix"]
    linkage_method = hierarchical_result.get("linkage_method", "unknown")
    distance_metric = hierarchical_result.get("distance_metric", "unknown")

    # If n_clusters specified but not cut_height, calculate cut height
    # Height at which to cut is the (n-k)th merge distance
    if n_clusters is not None and cut_height is None:
        n_samples = linkage_matrix.shape[0] + 1
        if n_clusters < n_samples:
            # Get the merge distance for the (n - k)th merge
            cut_height = linkage_matrix[-(n_clusters - 1), 2]

    # Create dendrogram
    dendro = dendrogram(
        linkage_matrix,
        ax=ax,
        labels=labels,
        color_threshold=color_threshold,
        above_threshold_color="gray",
    )

    # Add cut line if specified
    if cut_height is not None:
        ax.axhline(
            y=cut_height,
            color="red",
            linestyle="--",
            linewidth=2,
            label=f"Cut height: {cut_height:.2f}",
        )
        if n_clusters is not None:
            ax.legend(loc="upper right")

    # Formatting
    ax.set_xlabel("Sample Index" if labels is None else "Sample")
    ax.set_ylabel(f"Distance ({distance_metric})")

    # Add method info to title
    title_with_method = f"{title}\nMethod: {linkage_method}"
    if n_clusters is not None:
        title_with_method += f" | {n_clusters} clusters"

    ax.set_title(title_with_method)
    ax.grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    return fig
