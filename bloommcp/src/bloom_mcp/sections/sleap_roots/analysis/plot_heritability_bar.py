"""plot_heritability_bar — bar plot of heritability (H2) estimates.

Delegates the heritability calculation to
``sleap_roots_analyze.statistics.calculate_heritability_estimates`` and rendering
to ``sleap_roots_analyze.visualization.create_heritability_plot``; this file owns
no analysis or plotting logic of its own.

``create_heritability_plot`` returns a single ``Figure`` for small trait counts but
a ``list[Figure]`` once the trait count exceeds its internal pagination threshold
(currently 50 traits/page) -- see ``_viz_shared.save_plot_or_plots``.
"""

from pathlib import Path

from sleap_roots_analyze import statistics as stats_module
from sleap_roots_analyze.visualization import create_heritability_plot
from bloom_mcp.experiment_utils import load_experiment_data as _load_data
from bloom_mcp.tools._plots import FIGURE_REGISTRY_LOCK

from ._viz_shared import save_plot_or_plots, validate_filename


def plot_heritability_bar(filename: str, threshold: float = 0.5) -> str:
    """Generate a bar plot of heritability (H2) estimates for all traits.

    Requires heritability data — either from a prior calculate_heritability run
    or computes it on the fly. Highlights traits above the threshold.

    Args:
        filename: experiment identifier from list_available_experiments
        threshold: H2 threshold line to highlight (default 0.5)
    """
    unsafe = validate_filename(filename)
    if unsafe:
        return unsafe

    try:
        df, trait_cols, config, source = _load_data(filename)
    except Exception:
        return f"Could not load {filename!r}: the experiment data could not be read."
    if df is None:
        return source

    stem = Path(filename).stem
    genotype_col = config["genotype_col"]
    replicate_col = config["replicate_col"]

    if not genotype_col or not replicate_col:
        return f"Heritability requires genotype and replicate columns. Detected: genotype={genotype_col}, replicate={replicate_col}"

    h2_results = stats_module.calculate_heritability_estimates(
        df,
        trait_cols,
        genotype_col=genotype_col,
        replicate_col=replicate_col,
    )

    if "error" in h2_results:
        return f"Heritability calculation failed: {h2_results['error']}"

    try:
        # FIGURE_REGISTRY_LOCK: allocates a figure against the shared global matplotlib
        # registry, which a concurrent umap_analysis/pca_analysis call's
        # allocate-then-raise cleanup could otherwise mistake for its own (#721 PR
        # review — see that lock's own comment in bloom_mcp.tools._plots).
        with FIGURE_REGISTRY_LOCK:
            fig = create_heritability_plot(h2_results, threshold=threshold)
    except Exception:
        return "Heritability plot failed: the plot could not be generated for the computed estimates."

    url = save_plot_or_plots(fig, f"heritability_{stem}.png")

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
