"""plot_trait_boxplots — boxplots of trait values grouped by genotype.

Delegates rendering to
``sleap_roots_analyze.visualization.create_trait_boxplots_by_genotype`` (or its
``_batched`` counterpart above ``TRAIT_BATCH_THRESHOLD`` traits); this file owns no
plotting logic of its own.
"""

from pathlib import Path

from sleap_roots_analyze.visualization import (
    create_trait_boxplots_by_genotype,
    create_trait_boxplots_by_genotype_batched,
)
from bloom_mcp.experiment_utils import load_experiment_data as _load_data

from ._viz_shared import (
    TRAIT_BATCH_THRESHOLD,
    parse_traits,
    save_plot_or_plots,
    validate_filename,
)


def plot_trait_boxplots(filename: str, traits: str = "") -> str:
    """Generate boxplots of trait values grouped by genotype.

    Shows distribution per genotype for each trait. Useful for visual
    comparison of genotype effects and identifying outlier genotypes.

    Args:
        filename: experiment identifier from list_available_experiments
        traits: Comma-separated trait names (empty = all traits)
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

    if not genotype_col:
        return f"No genotype column detected in '{filename}'. Cannot group by genotype."

    selected = parse_traits(traits, trait_cols)
    if not selected:
        return "No valid traits found."

    try:
        if len(selected) > TRAIT_BATCH_THRESHOLD:
            fig_or_figs = create_trait_boxplots_by_genotype_batched(
                df, selected, genotype_col=genotype_col
            )
        else:
            fig_or_figs = create_trait_boxplots_by_genotype(
                df, selected, genotype_col=genotype_col
            )
    except Exception:
        return "Boxplot generation failed: the plot could not be generated for the selected traits."

    url = save_plot_or_plots(fig_or_figs, f"boxplots_{stem}.png")
    return (
        f"Trait Boxplots by Genotype: {stem} (source: {source})\n"
        f"  {len(selected)} traits plotted\n"
        f"  Plot saved: {url}"
    )
