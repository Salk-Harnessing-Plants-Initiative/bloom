"""Shared helpers for the 3 sleap_roots plotting tools (one file per tool).

Single-sourced here (mirrors ``tools/_qc_shared.py``'s rationale) so the plot files can't
silently desync on how a trait selection gets resolved or where the batching boundary sits.

Only two things live here now: ``TRAIT_BATCH_THRESHOLD`` and :func:`resolve_trait_columns`,
shared by the 3 tools #466 converged onto ``@as_mcp_tool`` (``plot_trait_histograms``,
``plot_trait_boxplots``, ``plot_correlation_matrix``). The pre-#466 generation of helpers —
``save_plot``/``save_plot_or_plots`` (write a PNG to ``PLOTS_DIR`` and return a URL),
``parse_traits``, ``validate_filename`` — served only the bare-``mcp.tool()`` plot tools, and
#462 deleted them together with the last two of those (``plot_heritability_bar``,
``plot_variance_decomposition``, retired into ``heritability_analysis``). Nothing in
``bloom_mcp`` writes to ``PLOTS_DIR`` any more; the directory's remaining plumbing
(static mount, env validation, compose bind-mount) is a separate retirement.
"""

from collections import Counter

from bloom_mcp.contract import BloomMCPError
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
# tests/tools/test_viz_shared.py, which asserts this against the live delegate
# signature so a future sleap-roots-analyze bump that changes that default is
# caught here rather than silently desyncing the two.
TRAIT_BATCH_THRESHOLD = 50


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
