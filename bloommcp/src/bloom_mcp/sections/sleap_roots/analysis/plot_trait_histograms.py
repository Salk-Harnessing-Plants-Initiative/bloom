"""plot_trait_histograms — histogram plots for trait distributions.

Delegates rendering to ``sleap_roots_analyze.visualization.create_trait_histograms``;
this file owns no plotting logic of its own.
"""

from pathlib import Path

from sleap_roots_analyze.visualization import create_trait_histograms
from bloom_mcp.experiment_utils import load_experiment_data as _load_data

from ._viz_shared import parse_traits, save_plot, validate_filename


def plot_trait_histograms(filename: str, traits: str = "") -> str:
    """Generate histogram plots showing the distribution of trait values.

    Creates a grid of histograms for each trait. Useful for checking
    normality, skewness, and identifying unusual distributions.

    Args:
        filename: CSV filename from list_available_experiments
        traits: Comma-separated trait names (empty = all traits)
    """
    unsafe = validate_filename(filename)
    if unsafe:
        return unsafe

    try:
        df, trait_cols, config, source = _load_data(filename)
    except Exception:
        return f"Could not load {filename!r}: the file could not be read as a CSV."
    if df is None:
        return source

    stem = Path(filename).stem

    selected = parse_traits(traits, trait_cols)
    if not selected:
        return "No valid traits found."

    try:
        fig = create_trait_histograms(df, selected)
    except Exception:
        return "Histogram generation failed: the plot could not be generated for the selected traits."

    url = save_plot(fig, f"histograms_{stem}.png")
    return (
        f"Trait Histograms: {stem} (source: {source})\n"
        f"  {len(selected)} traits plotted\n"
        f"  Plot saved: {url}"
    )
