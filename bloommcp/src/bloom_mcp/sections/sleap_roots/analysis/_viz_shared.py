"""Shared helpers for the 3 sleap_roots plotting tools (one file per tool).

Single-sourced here (mirrors ``tools/_qc_shared.py``'s rationale) so the 3 plot
files can't silently desync on how a figure gets saved or how a trait list gets
parsed.

Was 5 until bloom#462 retired ``plot_heritability_bar`` and
``plot_variance_decomposition`` into ``heritability_analysis``. That tool does NOT
route through this module: it persists figures through the ``ResultStore`` port like
every other granular consumer, and its pagination goes through
``tools/_plots.generate_figures`` instead of ``save_plot_or_plots`` below.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from bloom_mcp.experiment_utils import PLOTS_DIR, PLOTS_URL

# Trait count above which plot_trait_histograms/plot_trait_boxplots switch to their
# delegate's *_batched variant (list[Figure]) instead of rendering every trait into
# one figure. This only decides WHETHER to batch -- it is not the resulting page
# size. Each *_batched delegate (create_trait_histograms_batched,
# create_trait_boxplots_by_genotype_batched) has its own independent batch_size
# parameter (currently 16), so e.g. cylinder's 846 traits produce 53 pages of ~16
# traits each, not "TRAIT_BATCH_THRESHOLD traits per page". Set to 50 to match
# create_heritability_plot's own internal traits_per_page default for consistency
# across every plot path that can hit this scale. create_heritability_plot is no longer
# one of THIS module's callers (bloom#462 moved it behind heritability_analysis and
# tools/_plots.generate_figures), but keeping the two numbers equal still means one
# trait count produces one pagination behavior wherever a plot is rendered -- see
# test_trait_batch_threshold_matches_heritability_plot_default in
# tests/tools/test_viz_tools.py, which asserts this against the live delegate
# signature so a future sleap-roots-analyze bump that changes that default is
# caught here rather than silently desyncing the two.
TRAIT_BATCH_THRESHOLD = 50


def save_plot(fig, plot_name: str) -> str:
    """Save figure and return URL."""
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    plot_path = PLOTS_DIR / plot_name
    fig.savefig(plot_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return f"{PLOTS_URL}/{plot_name}"


def save_plot_or_plots(fig_or_figs, plot_name: str) -> str:
    """Save a single figure, or a paginated list of figures, and return the URL(s).

    Both remaining callers (``plot_trait_histograms``, ``plot_trait_boxplots``) switch
    to a ``*_batched`` delegate above ``TRAIT_BATCH_THRESHOLD`` and get back a
    ``list[Figure]``; below it they get a single ``Figure``. Save each page with a
    numbered suffix and return a summary of all URLs rather than crashing on
    ``list.savefig``.

    Added in #483 for ``create_heritability_plot``, whose own ``traits_per_page``
    pagination first exposed the crash once the cylinder fixture (846 traits) was wide
    enough to reach it. That tool is gone (bloom#462); the same shape now lives in
    ``tools/_plots.generate_figures``, which expands a paginated return into
    ``<key>_page<N>`` entries for ``heritability_analysis``. Kept here for the two
    batched trait plotters, which still need it.
    """
    if isinstance(fig_or_figs, list):
        stem = Path(plot_name).stem
        suffix = Path(plot_name).suffix
        urls = [
            save_plot(fig, f"{stem}_page{i}{suffix}")
            for i, fig in enumerate(fig_or_figs, start=1)
        ]
        return f"{len(urls)} pages: " + ", ".join(urls)
    return save_plot(fig_or_figs, plot_name)


def parse_traits(traits: str, available: list) -> list:
    """Parse comma-separated trait list, return filtered list."""
    if not traits.strip():
        return available
    requested = [t.strip() for t in traits.split(",")]
    return [t for t in requested if t in available]


def validate_filename(filename: str) -> str | None:
    """Return an error message if ``filename`` is not a bare experiment identifier, else
    ``None``.

    ``filename`` flows into ``TRAITS_DIR / filename`` + ``pd.read_csv`` (via
    ``load_experiment_data``), so a path with separators or ``..`` (or an absolute
    path) would read outside ``TRAITS_DIR`` — and its contents could then surface
    in the tool's returned summary. Require a bare basename (Phase 3 / P3.3).

    Mirrors ``tools/_qc_shared._validate_experiment_name``'s check, but returns a
    plain string instead of raising: these 3 tools register via bare ``mcp.tool()``
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
        return "filename must be a bare experiment identifier (no path separators)."
    return None
