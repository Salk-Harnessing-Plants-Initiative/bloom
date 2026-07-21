"""Shared helpers for the 5 sleap_roots plotting tools (one file per tool).

Single-sourced here (mirrors ``tools/_qc_shared.py``'s rationale) so the 5 plot
files can't silently desync on how a figure gets saved or how a trait list gets
parsed.
"""

from pathlib import Path

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


def validate_filename(filename: str) -> str | None:
    """Return an error message if ``filename`` is not a bare CSV filename, else ``None``.

    ``filename`` flows into ``TRAITS_DIR / filename`` + ``pd.read_csv`` (via
    ``load_experiment_data``), so a path with separators or ``..`` (or an absolute
    path) would read outside ``TRAITS_DIR`` — and its contents could then surface
    in the tool's returned summary. Require a bare basename (Phase 3 / P3.3).

    Mirrors ``tools/_qc_shared._validate_experiment_name``'s check, but returns a
    plain string instead of raising: these 5 tools register via bare ``mcp.tool()``
    and return plain strings end-to-end, not ``BloomMCPError`` — nothing here
    catches that exception type, so reusing the raising guard directly would let
    it escape uncaught to FastMCP's generic handler.

    ``Path(filename).name != filename`` alone is not enough: ``pathlib.Path`` only
    treats ``\\`` as a separator on Windows, so on POSIX (the deploy target)
    ``Path("..\\\\secret.csv").name`` equals the input unchanged and the traversal
    payload would fall through to a "file not found" read attempt instead of being
    rejected here. Check for either separator explicitly so the guard doesn't
    depend on which platform it runs on.
    """
    if (
        filename in ("", ".", "..")
        or "/" in filename
        or "\\" in filename
        or Path(filename).name != filename
    ):
        return "filename must be a bare CSV filename (no path separators)."
    return None
