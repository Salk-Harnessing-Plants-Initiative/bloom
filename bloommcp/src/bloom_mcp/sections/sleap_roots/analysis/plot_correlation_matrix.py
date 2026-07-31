"""plot_correlation_matrix — correlation heatmap for trait relationships.

Delegates rendering to
``sleap_roots_analyze.visualization.create_correlation_heatmap``; this file owns
no plotting logic of its own (the reported high-correlation counts are a plain
``pandas`` summary of the same selection, not a re-implementation of the plot).
"""

from pathlib import Path

import numpy as np
from sleap_roots_analyze.visualization import create_correlation_heatmap
from bloom_mcp.experiment_utils import load_experiment_data as _load_data

from ._viz_shared import parse_traits, save_plot, validate_filename


def plot_correlation_matrix(filename: str, traits: str = "") -> str:
    """Generate a correlation heatmap for trait relationships.

    Shows pairwise Pearson correlations between traits. Useful for
    identifying redundant traits and discovering trait relationships.

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
        return f"Could not load {filename!r}: the file could not be read as a CSV."
    if df is None:
        return source

    stem = Path(filename).stem

    selected = parse_traits(traits, trait_cols)
    if not selected:
        return "No valid traits found."

    try:
        fig = create_correlation_heatmap(df, selected)
    except Exception:
        return "Correlation heatmap failed: the plot could not be generated for the selected traits."

    url = save_plot(fig, f"correlation_matrix_{stem}.png")

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
