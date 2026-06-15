"""
MCP Tool Wrappers for SLEAP Visualization.

Wraps functions from source/visualization.py, source/cluster_visualization.py, and
source/outlier_visualization.py. Uses source/experiment_utils.py for dynamic experiment
discovery and column auto-detection.
"""

import json
import numpy as np
import pandas as pd
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from source.visualization import (
    create_trait_histograms,
    create_trait_boxplots_by_genotype,
    create_correlation_heatmap,
    create_heritability_plot,
    create_variance_decomposition_plot,
)
from source.cluster_visualization import create_dendrogram
from source.outlier_visualization import create_comprehensive_outlier_comparison
from source.experiment_utils import load_experiment_data as _load_data, OUTPUT_DIR, PLOTS_DIR, PLOTS_URL


def _save_plot(fig, plot_name: str) -> str:
    """Save figure and return URL."""
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    plot_path = PLOTS_DIR / plot_name
    fig.savefig(plot_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return f"{PLOTS_URL}/{plot_name}"


def _parse_traits(traits: str, available: list) -> list:
    """Parse comma-separated trait list, return filtered list."""
    if not traits.strip():
        return available
    requested = [t.strip() for t in traits.split(",")]
    return [t for t in requested if t in available]


# ============================================================================
# Tool 1: Trait Histograms
# ============================================================================

def plot_trait_histograms(filename: str, traits: str = "") -> str:
    """Generate histogram plots showing the distribution of trait values.

    Creates a grid of histograms for each trait. Useful for checking
    normality, skewness, and identifying unusual distributions.

    Args:
        filename: CSV filename from list_available_experiments
        traits: Comma-separated trait names (empty = all traits)
    """
    df, trait_cols, config, source = _load_data(filename)
    if df is None:
        return source

    stem = Path(filename).stem

    selected = _parse_traits(traits, trait_cols)
    if not selected:
        return "No valid traits found."

    try:
        fig = create_trait_histograms(df, selected)
    except Exception as e:
        return f"Histogram generation failed: {e}"

    url = _save_plot(fig, f"histograms_{stem}.png")
    return (
        f"Trait Histograms: {stem} (source: {source})\n"
        f"  {len(selected)} traits plotted\n"
        f"  Plot saved: {url}"
    )


# ============================================================================
# Tool 2: Trait Boxplots by Genotype
# ============================================================================

def plot_trait_boxplots(filename: str, traits: str = "") -> str:
    """Generate boxplots of trait values grouped by genotype.

    Shows distribution per genotype for each trait. Useful for visual
    comparison of genotype effects and identifying outlier genotypes.

    Args:
        filename: CSV filename from list_available_experiments
        traits: Comma-separated trait names (empty = all traits)
    """
    df, trait_cols, config, source = _load_data(filename)
    if df is None:
        return source

    stem = Path(filename).stem
    genotype_col = config["genotype_col"]

    if not genotype_col:
        return f"No genotype column detected in '{filename}'. Cannot group by genotype."

    selected = _parse_traits(traits, trait_cols)
    if not selected:
        return "No valid traits found."

    try:
        fig = create_trait_boxplots_by_genotype(
            df, selected, genotype_col=genotype_col,
        )
    except Exception as e:
        return f"Boxplot generation failed: {e}"

    url = _save_plot(fig, f"boxplots_{stem}.png")
    return (
        f"Trait Boxplots by Genotype: {stem} (source: {source})\n"
        f"  {len(selected)} traits plotted\n"
        f"  Plot saved: {url}"
    )


# ============================================================================
# Tool 3: Correlation Matrix
# ============================================================================

def plot_correlation_matrix(filename: str, traits: str = "") -> str:
    """Generate a correlation heatmap for trait relationships.

    Shows pairwise Pearson correlations between traits. Useful for
    identifying redundant traits and discovering trait relationships.

    Args:
        filename: CSV filename from list_available_experiments
        traits: Comma-separated trait names (empty = all traits)
    """
    df, trait_cols, config, source = _load_data(filename)
    if df is None:
        return source

    stem = Path(filename).stem

    selected = _parse_traits(traits, trait_cols)
    if not selected:
        return "No valid traits found."

    try:
        fig = create_correlation_heatmap(df, selected)
    except Exception as e:
        return f"Correlation heatmap failed: {e}"

    url = _save_plot(fig, f"correlation_matrix_{stem}.png")

    corr = df[selected].corr()
    upper = corr.where(np.triu(np.ones(corr.shape), k=1).astype(bool))
    high_pos = (upper > 0.7).sum().sum()
    high_neg = (upper < -0.7).sum().sum()

    return (
        f"Correlation Matrix: {stem} (source: {source})\n"
        f"  {len(selected)} traits\n"
        f"  Strong positive correlations (>0.7): {high_pos}\n"
        f"  Strong negative correlations (<-0.7): {high_neg}\n"
        f"  Plot saved: {url}"
    )


# ============================================================================
# Tool 4: Heritability Bar Plot
# ============================================================================

def plot_heritability_bar(filename: str, threshold: float = 0.5) -> str:
    """Generate a bar plot of heritability (H2) estimates for all traits.

    Requires heritability data — either from a prior calculate_heritability run
    or computes it on the fly. Highlights traits above the threshold.

    Args:
        filename: CSV filename from list_available_experiments
        threshold: H2 threshold line to highlight (default 0.5)
    """
    df, trait_cols, config, source = _load_data(filename)
    if df is None:
        return source

    stem = Path(filename).stem
    genotype_col = config["genotype_col"]
    replicate_col = config["replicate_col"]

    if not genotype_col or not replicate_col:
        return f"Heritability requires genotype and replicate columns. Detected: genotype={genotype_col}, replicate={replicate_col}"

    from source import trait_statistics as stats_module
    h2_results = stats_module.calculate_heritability_estimates(
        df, trait_cols,
        genotype_col=genotype_col,
        replicate_col=replicate_col,
    )

    if "error" in h2_results:
        return f"Heritability calculation failed: {h2_results['error']}"

    try:
        fig = create_heritability_plot(h2_results, threshold=threshold)
    except Exception as e:
        return f"Heritability plot failed: {e}"

    url = _save_plot(fig, f"heritability_{stem}.png")

    above = sum(
        1 for t in trait_cols
        if "heritability" in h2_results.get(t, {})
        and h2_results[t]["heritability"] >= threshold
    )

    return (
        f"Heritability Bar Plot: {stem} (source: {source})\n"
        f"  {len(trait_cols)} traits, {above} above H2 >= {threshold}\n"
        f"  Plot saved: {url}"
    )


# ============================================================================
# Tool 5: Variance Decomposition
# ============================================================================

def plot_variance_decomposition(filename: str) -> str:
    """Generate variance decomposition plot (genetic vs environmental variance).

    Shows stacked bars for each trait decomposing total variance into
    genetic (between-genotype) and environmental (within-genotype) components.

    Args:
        filename: CSV filename from list_available_experiments
    """
    df, trait_cols, config, source = _load_data(filename)
    if df is None:
        return source

    stem = Path(filename).stem
    genotype_col = config["genotype_col"]
    replicate_col = config["replicate_col"]

    if not genotype_col or not replicate_col:
        return f"Variance decomposition requires genotype and replicate columns. Detected: genotype={genotype_col}, replicate={replicate_col}"

    from source import trait_statistics as stats_module
    h2_results = stats_module.calculate_heritability_estimates(
        df, trait_cols,
        genotype_col=genotype_col,
        replicate_col=replicate_col,
    )

    if "error" in h2_results:
        return f"Heritability calculation failed: {h2_results['error']}"

    rows = []
    for trait in trait_cols:
        r = h2_results.get(trait, {})
        if "heritability" in r:
            rows.append({
                "trait": trait,
                "H2": r["heritability"],
                "var_genetic": r.get("var_genetic", 0),
                "var_residual": r.get("var_residual", 0),
            })

    if not rows:
        return "No valid heritability results to plot."

    comparison_df = pd.DataFrame(rows)

    try:
        fig = create_variance_decomposition_plot(comparison_df)
    except Exception as e:
        return f"Variance decomposition plot failed: {e}"

    url = _save_plot(fig, f"variance_decomposition_{stem}.png")
    return (
        f"Variance Decomposition: {stem} (source: {source})\n"
        f"  {len(rows)} traits plotted\n"
        f"  Plot saved: {url}"
    )


# ============================================================================
# Tool 8: Hierarchical Clustering Dendrogram
# ============================================================================

def plot_dendrogram(
    filename: str,
    n_clusters: int = 3,
    linkage_method: str = "ward",
) -> str:
    """Generate a hierarchical clustering dendrogram.

    Shows the hierarchical structure of sample relationships with a cut line
    at the specified number of clusters.

    Args:
        filename: CSV filename from list_available_experiments
        n_clusters: Number of clusters to indicate with cut line (default 3)
        linkage_method: Linkage method (ward, complete, average, single)
    """
    df, trait_cols, config, source = _load_data(filename)
    if df is None:
        return source

    stem = Path(filename).stem

    from source.clustering import perform_hierarchical_clustering, cut_dendrogram as cut_dendro
    try:
        hier_result = perform_hierarchical_clustering(
            data=df[trait_cols], method=linkage_method, standardize=True,
        )
        cut_result = cut_dendro(hier_result, n_clusters=n_clusters)
    except Exception as e:
        return f"Hierarchical clustering failed: {e}"

    try:
        fig = create_dendrogram(
            hier_result,
            cut_height=cut_result["cut_height"],
            n_clusters=n_clusters,
        )
    except Exception as e:
        return f"Dendrogram plot failed: {e}"

    url = _save_plot(fig, f"dendrogram_{stem}_{linkage_method}.png")
    return (
        f"Dendrogram: {stem} (source: {source})\n"
        f"  Linkage: {linkage_method}, k={n_clusters}\n"
        f"  Cophenetic correlation: {hier_result['cophenetic_correlation']:.3f}\n"
        f"  Plot saved: {url}"
    )


# ============================================================================
# Tool 9: Multi-method Outlier Comparison
# ============================================================================

def plot_outlier_comparison(filename: str) -> str:
    """Generate a multi-method outlier comparison plot.

    Requires prior outlier detection runs. Reads saved results from
    BLOOM_OUTPUT_DIR and creates a comparison visualization showing
    which samples are flagged by each method.

    Args:
        filename: CSV filename from list_available_experiments
    """
    stem = Path(filename).stem
    out_dir = OUTPUT_DIR / f"outliers_{stem}"
    if not out_dir.exists():
        return (
            f"No outlier detection results found for '{stem}'. "
            "Run outlier detection tools first."
        )

    outlier_results = {}
    method_files = {
        "mahalanobis": "mahalanobis_outliers.json",
        "isolation_forest": "isolation_forest_outliers.json",
        "pca": "pca_outliers.json",
    }

    for method, fname in method_files.items():
        json_path = out_dir / fname
        if json_path.exists():
            data = json.loads(json_path.read_text())
            outlier_results[method] = {
                "outlier_indices": data.get("outlier_indices", data.get("consensus_outliers", [])),
                "n_outliers": data.get("n_outliers", data.get("n_consensus_outliers", 0)),
            }

    if not outlier_results:
        return f"No outlier detection results found in {out_dir}. Run detection tools first."

    try:
        fig = create_comprehensive_outlier_comparison(outlier_results)
    except Exception as e:
        return f"Outlier comparison plot failed: {e}"

    url = _save_plot(fig, f"outlier_comparison_{stem}.png")

    lines = [
        f"Outlier Comparison: {stem}",
        f"  Methods compared: {', '.join(outlier_results.keys())}",
    ]
    for method, result in outlier_results.items():
        lines.append(f"    {method}: {result['n_outliers']} outliers")

    lines.append(f"\n  Plot saved: {url}")
    return "\n".join(lines)


# ============================================================================
# Registration
# ============================================================================

def register(mcp):
    """Register all visualization tools with the MCP server."""
    mcp.tool()(plot_trait_histograms)
    mcp.tool()(plot_trait_boxplots)
    mcp.tool()(plot_correlation_matrix)
    mcp.tool()(plot_heritability_bar)
    mcp.tool()(plot_variance_decomposition)
    # plot_pca_scree and plot_pca_biplot are registered by dimred_tools
    mcp.tool()(plot_dendrogram)
    mcp.tool()(plot_outlier_comparison)
