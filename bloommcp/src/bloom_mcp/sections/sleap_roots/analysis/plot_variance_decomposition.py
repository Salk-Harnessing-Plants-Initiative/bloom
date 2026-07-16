"""plot_variance_decomposition — genetic vs environmental variance decomposition.

Delegates heritability calculation to
``sleap_roots_analyze.statistics.calculate_heritability_estimates``, the
comparison-table shape to ``compare_trait_heritabilities`` (the same helper
``create_variance_decomposition_plot`` expects — its own docstring shows its
output feeding this exact plot), and rendering to
``sleap_roots_analyze.visualization.create_variance_decomposition_plot``. This file
owns no analysis or plotting logic of its own.
"""

from pathlib import Path

from sleap_roots_analyze import statistics as stats_module
from sleap_roots_analyze.visualization import create_variance_decomposition_plot
from bloom_mcp.experiment_utils import load_experiment_data as _load_data

from ._viz_shared import save_plot


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

    h2_results = stats_module.calculate_heritability_estimates(
        df,
        trait_cols,
        genotype_col=genotype_col,
        replicate_col=replicate_col,
    )

    if "error" in h2_results:
        return f"Heritability calculation failed: {h2_results['error']}"

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

    url = save_plot(fig, f"variance_decomposition_{stem}.png")
    return (
        f"Variance Decomposition: {stem} (source: {source})\n"
        f"  {len(comparison_df)} traits plotted\n"
        f"  Plot saved: {url}"
    )
