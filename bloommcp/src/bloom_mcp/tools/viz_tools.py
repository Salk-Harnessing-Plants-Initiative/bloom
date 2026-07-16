"""
MCP Tool Wrappers for SLEAP Visualization.

Wraps sleap_roots_analyze.visualization directly (5 surviving standalone plots —
histograms, boxplots, correlation matrix, heritability bar, variance decomposition).
Uses bloom_mcp/experiment_utils.py for dynamic experiment discovery and column
auto-detection. `plot_dendrogram` and `plot_outlier_comparison` were dropped: the
former computes hierarchical clustering internally rather than consuming the
granular `clustering` tool's persisted output, and the latter's only input source
(the retired outlier workflow's JSON) no longer exists.
"""

import numpy as np
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from sleap_roots_analyze.visualization import (
    create_trait_histograms,
    create_trait_boxplots_by_genotype,
    create_correlation_heatmap,
    create_heritability_plot,
    create_variance_decomposition_plot,
)
from bloom_mcp.experiment_utils import (
    load_experiment_data as _load_data,
    PLOTS_DIR,
    PLOTS_URL,
)


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
            df,
            selected,
            genotype_col=genotype_col,
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

    from sleap_roots_analyze import statistics as stats_module

    h2_results = stats_module.calculate_heritability_estimates(
        df,
        trait_cols,
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
        1
        for t in trait_cols
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

    from sleap_roots_analyze import statistics as stats_module

    h2_results = stats_module.calculate_heritability_estimates(
        df,
        trait_cols,
        genotype_col=genotype_col,
        replicate_col=replicate_col,
    )

    if "error" in h2_results:
        return f"Heritability calculation failed: {h2_results['error']}"

    # Delegate the comparison-table shape to the same upstream helper
    # create_variance_decomposition_plot expects (compare_trait_heritabilities'
    # own docstring shows its output feeding this exact plot) rather than hand-rolling
    # a subset of its columns — the hand-rolled version was a pre-existing bug (wrong
    # column name, and missing columns the delegate reads), so every call raised a
    # KeyError; this tool never actually produced a plot before this fix.
    comparison_df = stats_module.compare_trait_heritabilities(
        df,
        trait_cols,
        h2_results,
        genotype_col=genotype_col,
        replicate_col=replicate_col,
    )

    # Traits calculate_heritability_estimates couldn't score land as NaN rows in
    # compare_trait_heritabilities' output — exclude them (mirrors the tool's prior
    # "only traits with a heritability result" filter).
    comparison_df = comparison_df[comparison_df["heritability"].notna()]

    # Fail loudly rather than plot a silently-zero-filled bar: a scored trait with a
    # missing variance component means the delegated contract changed shape.
    inconsistent = comparison_df[
        comparison_df["var_genetic"].isna() | comparison_df["var_residual"].isna()
    ]
    if not inconsistent.empty:
        return (
            "Variance decomposition unavailable: heritability result for "
            f"{list(inconsistent['trait'])} is missing var_genetic/var_residual — "
            "the sleap-roots-analyze return contract changed. Refusing to plot a "
            "zero-filled decomposition."
        )

    if comparison_df.empty:
        return "No valid heritability results to plot."

    try:
        fig = create_variance_decomposition_plot(comparison_df)
    except Exception as e:
        return f"Variance decomposition plot failed: {e}"

    url = _save_plot(fig, f"variance_decomposition_{stem}.png")
    return (
        f"Variance Decomposition: {stem} (source: {source})\n"
        f"  {len(comparison_df)} traits plotted\n"
        f"  Plot saved: {url}"
    )


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
