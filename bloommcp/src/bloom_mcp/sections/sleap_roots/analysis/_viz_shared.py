"""Shared helpers for the 5 sleap_roots plotting tools (one file per tool).

Single-sourced here (mirrors ``tools/_qc_shared.py``'s rationale) so the 5 plot
files can't silently desync on how a figure gets saved or how a trait list gets
parsed.
"""

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from bloom_mcp.experiment_utils import PLOTS_DIR, PLOTS_URL


def save_plot(fig, plot_name: str) -> str:
    """Save figure and return URL."""
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    plot_path = PLOTS_DIR / plot_name
    fig.savefig(plot_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return f"{PLOTS_URL}/{plot_name}"


def parse_traits(traits: str, available: list) -> list:
    """Parse comma-separated trait list, return filtered list."""
    if not traits.strip():
        return available
    requested = [t.strip() for t in traits.split(",")]
    return [t for t in requested if t in available]
