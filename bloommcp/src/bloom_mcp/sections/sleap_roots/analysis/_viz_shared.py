"""Shared helpers for the sleap_roots plotting tools (one file per tool).

Single-sourced here (mirrors ``tools/_qc_shared.py``'s rationale) so the plot files can't
silently desync on how a figure gets saved or how a trait list gets resolved.

Two generations of tool coexist here post-#466: ``save_plot``/``save_plot_or_plots``/
``parse_traits``/``validate_filename`` remain used by the 2 tools still on the pre-#466 bare
``mcp.tool()`` pattern (``plot_heritability_bar``, ``plot_variance_decomposition`` — retiring
into ``heritability_analysis`` per #462). ``TRAIT_BATCH_THRESHOLD`` and
:func:`resolve_trait_columns` are shared with (and, for the latter, exclusively used by) the 3
tools #466 converged onto ``@as_mcp_tool`` (``plot_trait_histograms``, ``plot_trait_boxplots``,
``plot_correlation_matrix``), which resolve their trait selection via
:func:`resolve_trait_columns` instead of ``parse_traits``/``validate_filename``.
"""

from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from bloom_mcp.contract import BloomMCPError
from bloom_mcp.experiment_utils import PLOTS_DIR, PLOTS_URL
from bloom_mcp.tools._qc_shared import _validate_trait_subset

# Trait count above which plot_trait_histograms/plot_trait_boxplots switch to their
# delegate's *_batched variant (list[Figure]) instead of rendering every trait into
# one figure. This only decides WHETHER to batch -- it is not the resulting page
# size. Each *_batched delegate (create_trait_histograms_batched,
# create_trait_boxplots_by_genotype_batched) has its own independent batch_size
# parameter (currently 16), so e.g. cylinder's 846 traits produce 53 pages of ~16
# traits each, not "TRAIT_BATCH_THRESHOLD traits per page". Set to 50 to match
# create_heritability_plot's own internal traits_per_page default for consistency
# across all plot tools that can hit this scale -- see
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

    ``create_heritability_plot`` returns a single ``Figure`` for small trait counts
    but a ``list[Figure]`` once the trait count exceeds its internal
    ``traits_per_page`` (currently 50) -- turface_19's ~18-20 traits never crosses
    that threshold, so this path was never exercised until #483 added a fixture wide
    enough (cylinder, 846 traits) to reach it. Save each page with a numbered suffix
    and return a summary of all URLs rather than crashing on ``list.savefig``.
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
        return "filename must be a bare experiment identifier (no path separators)."
    return None


def resolve_trait_columns(
    frame, trait_columns: list[str] | None, experiment: str
) -> list[str]:
    """Resolve a caller-supplied ``trait_columns`` subset for a raw-frame viz tool.

    Shared by the 3 tools #466 converged onto ``@as_mcp_tool``
    (``plot_trait_histograms``/``plot_trait_boxplots``/``plot_correlation_matrix``) so the
    three files can't silently drift on this validation — previously reimplemented three
    times as each tool's own private ``_resolve_trait_cols`` (#466 review finding).

    - ``None`` -> every detected trait column.
    - ``[]`` (an explicit empty list) -> rejected (``invalid_input``); ambiguous with "all
      traits", so it must be named explicitly rather than silently treated as one or the
      other.
    - Existence + numeric dtype checked via ``_qc_shared._validate_trait_subset`` at its
      non-certified strictness level — the same one ``qc_clean``/``qc_inspect`` use for a
      raw (not cleaned-consumer) frame.
    - **Duplicate names are rejected here**, unlike ``_validate_trait_subset``'s
      non-certified branch, which intentionally tolerates duplicates for
      ``qc_clean``/``qc_inspect`` (harmless there). A duplicate is NOT harmless for these 3
      tools: ``plot_correlation_matrix`` would silently count a duplicated column's
      self-correlation (r=1.0) as a "strong positive correlation" in a result that is a
      permanent, provenance-stamped ``ResultStore`` artifact (not a transient string), and
      ``plot_trait_histograms``/``plot_trait_boxplots`` would render the same trait's panel
      twice.
    - The resolved set must be non-empty (``invalid_input`` naming ``experiment``) — a
      metadata-only frame with no detected numeric trait has nothing to plot/correlate.

    Matching (existence, numeric check, and the duplicate check above) is exact-string,
    case-sensitive, matching ``pandas`` column-lookup semantics — ``"Trait_A"`` and
    ``"trait_a"`` are different names, by design, not a bug a future reader should "fix" by
    adding case-folding.
    """
    if trait_columns is not None:
        if not trait_columns:
            raise BloomMCPError(
                code="invalid_input",
                message=f"trait_columns for {experiment!r} was given as an empty list.",
                remedy="Omit trait_columns to use all detected traits, or name at least "
                "one trait column.",
            )
        # Counter, not `[c for c in trait_columns if trait_columns.count(c) > 1]`: the
        # latter is O(n^2) (a .count() call per element over the same list), which matters
        # at cylinder's ~846-trait scale (#466 review).
        duplicates = sorted(c for c, n in Counter(trait_columns).items() if n > 1)
        if duplicates:
            raise BloomMCPError(
                code="invalid_input",
                message=(
                    f"trait_columns for {experiment!r} contains duplicate columns: "
                    f"{duplicates}."
                ),
                remedy="List each trait column at most once.",
            )
        _validate_trait_subset(frame, trait_columns, experiment)
    trait_cols = list(trait_columns or frame.trait_cols)
    if not trait_cols:
        raise BloomMCPError(
            code="invalid_input",
            message=f"No numeric trait columns detected in {experiment!r}.",
            remedy="Check the experiment has numeric trait columns, or pass trait_columns "
            "explicitly.",
        )
    return trait_cols
