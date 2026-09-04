"""plot_trait_histograms — histogram plots for trait distributions.

Delegates rendering to ``sleap_roots_analyze.visualization.create_trait_histograms``
(or its ``_batched`` counterpart above ``TRAIT_BATCH_THRESHOLD`` traits); this file
owns no plotting logic of its own.
"""

from pathlib import Path

from sleap_roots_analyze.visualization import (
    create_trait_histograms,
    create_trait_histograms_batched,
)
from bloom_mcp.experiment_utils import load_experiment_data as _load_data
from bloom_mcp.tools._plots import call_with_figure_cleanup

from ._viz_shared import (
    TRAIT_BATCH_THRESHOLD,
    parse_traits,
    save_plot_or_plots,
    validate_filename,
)


def plot_trait_histograms(filename: str, traits: str = "") -> str:
    """Generate histogram plots showing the distribution of trait values.

    Creates a grid of histograms for each trait. Useful for checking
    normality, skewness, and identifying unusual distributions.

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

    selected = parse_traits(traits, trait_cols)
    if not selected:
        return "No valid traits found."

    def _make_histograms():
        if len(selected) > TRAIT_BATCH_THRESHOLD:
            return create_trait_histograms_batched(df, selected)
        return create_trait_histograms(df, selected)

    try:
        # call_with_figure_cleanup: acquires the shared FIGURE_REGISTRY_LOCK around
        # this delegate call (#721 PR review) and closes any figure(s) it allocates
        # before raising, instead of leaking them — this file's own
        # `except Exception: return ...` below would otherwise swallow such an
        # exception without closing whatever was already rendered.
        fig_or_figs = call_with_figure_cleanup(_make_histograms)
    except Exception:
        return "Histogram generation failed: the plot could not be generated for the selected traits."

    url = save_plot_or_plots(fig_or_figs, f"histograms_{stem}.png")
    return (
        f"Trait Histograms: {stem} (source: {source})\n"
        f"  {len(selected)} traits plotted\n"
        f"  Plot saved: {url}"
    )
