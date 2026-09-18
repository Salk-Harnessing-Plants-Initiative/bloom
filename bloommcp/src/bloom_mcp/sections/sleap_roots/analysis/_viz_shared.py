"""Shared helpers for the 3 sleap_roots plotting tools (one file per tool).

Single-sourced here (mirrors ``tools/_qc_shared.py``'s rationale) so the plot files can't
silently desync on how a trait selection gets resolved or where the batching boundary sits.

``TRAIT_BATCH_THRESHOLD`` and :func:`resolve_trait_columns` are shared by the 3 tools #466
converged onto ``@as_mcp_tool`` (``plot_trait_histograms``, ``plot_trait_boxplots``,
``plot_correlation_matrix``). #748 added the sample-size disclosure helpers the two trait-plot
tools share — ``MIN_PLOTTED_SAMPLES``, the two reporting caps, :func:`native`, and the two
count tables — single-sourced for the same reason: the two tools must not drift on what counts
as a plotted observation. The pre-#466 generation of helpers —
``save_plot``/``save_plot_or_plots`` (write a PNG to ``PLOTS_DIR`` and return a URL),
``parse_traits``, ``validate_filename`` — served only the bare-``mcp.tool()`` plot tools, and
#462 deleted them together with the last two of those (``plot_heritability_bar``,
``plot_variance_decomposition``, retired into ``heritability_analysis``). Nothing in
``bloom_mcp`` writes to ``PLOTS_DIR`` any more; the directory's remaining plumbing
(static mount, env validation, compose bind-mount) is a separate retirement.
"""

import math
from collections import Counter

import numpy as np
import pandas as pd

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


# Minimum observations below which a rendered box is flagged as too thin to describe a
# distribution (#748). OWNED HERE, deliberately not an alias for
# _qc_shared._CANONICAL_MIN_SAMPLES_PER_TRAIT (10) -- the same decoupling #784 makes for
# plot_correlation_matrix's _MIN_CORR_OVERLAP, applied at birth rather than walked back later.
# That constant answers "enough samples to KEEP A TRAIT during cleaning", a per-column
# completeness convention; this one answers "enough points for a BOX to be made of data".
# Aliasing would let a QC-side retune silently move which boxes these tools flag and what
# their rendered note says.
#
# WHY 5. A box plot's five-number summary needs enough observations for its quartiles to BE
# observations rather than interpolations. Below n=5, matplotlib's default linear-interpolation
# quartiles are weighted blends of adjacent order statistics matching no measured plant: at n=2
# on [1, 2] the box spans [1.25, 1.75], containing neither datum. n=5 is the smallest n>1 at
# which Q1, the median and Q3 all land exactly on order statistics (x2, x3, x4) -- the first n
# at which the drawn box is made of data. Pinned as a property by
# test_five_is_the_first_sample_size_whose_quartiles_are_order_statistics.
#
# WHAT IT DOES NOT BUY YOU. It is a DEGENERACY floor, not a sufficiency threshold, and says
# nothing about the whiskers or the flier dots. Measured (60k replicates per n, clean standard
# normal, matplotlib's whis=1.5): no flier can be drawn at all below n=4, and at n=5 -- on the
# CLEARING side of this floor -- a sample shows at least one spurious "outlier" dot 33% of the
# time with 8.6% of its points flagged, against the ~0.7% asymptotic rate. The flagged fraction
# falls with n (4.0% at 10, 1.8% at 30) but the probability of at least one spurious flier does
# not: it sits between 27% and 34% at every n from 4 to 30. A flier on a thin box is an
# arithmetic artifact whether or not the box clears this floor.
#
# WHY NOT 10. Measured on tests/fixtures/turface_19_final_data.csv (19 genotypes, 7-9
# replicates, 11 detected traits, no nulls): a floor of 10 flags 209 of 209 (trait, genotype)
# cells; a floor of 5 flags none. A warning that fires on 100% of a healthy, complete
# experiment is one callers learn to ignore.
#
# For plot_trait_histograms the same constant carries NO distributional claim -- a histogram
# has no quartiles. There it is a bare "too few points for a shape to exist" floor.
MIN_PLOTTED_SAMPLES = 5

# Caps on the flagged-cell lists reported inline (#748). At cylinder scale (846 traits x ~19
# genotypes = 16,074 cells) an uncapped list is a denial of service against the caller's
# context, not a disclosure. Each list is ordered worst-first so the cap truncates the
# best-supported end, and each carries an uncapped count; the complete table always ships as a
# committed CSV output. 20 matches the sibling correlation tool's own per-pair caps.
MAX_FLAGGED_REPORTED = 20
# Names shown in the note drawn on the figure before it degrades to "+N more". Smaller than the
# list cap because the note has to stay readable on the image itself.
MAX_NOTE_NAMES = 10

# Column order of group_sample_size_table's output. Also the committed CSV's header, so it is
# named once rather than restated at each call site.
GROUP_TABLE_COLUMNS = [
    "trait",
    "genotype",
    "n_rows_in_group",
    "n_plotted",
    "n_finite",
    "n_non_finite",
    "n_missing",
    "nan_fraction",
]
TRAIT_TABLE_COLUMNS = [
    "trait",
    "n_plotted",
    "n_finite",
    "n_non_finite",
    "n_missing",
    "nan_fraction",
]


def native(value):
    """Coerce a numpy/pandas scalar to a JSON-safe native Python value (``None`` if not finite).

    Not belt-and-braces (#748): every count here comes out of ``groupby().count()``,
    ``np.median`` or ``isinf().sum()`` as ``np.int64``/``np.float64``, and ``manifest.py``'s
    ``stamped.model_dump(mode="json")`` raises ``PydanticSerializationError: Unable to serialize
    unknown type: <class 'numpy.int64'>`` on those — verified, not anticipated. These are the
    first tools in the family to stamp numeric aggregates into a run's ``params``
    (``plot_correlation_matrix`` stamps only strings and lists of strings; ``qc_inspect`` routes
    its numerics through ``convert_to_json_serializable`` first).

    Non-finite values become ``None`` rather than ``NaN``/``Infinity``: manifests are serialized
    by ``storage_backend._json_bytes`` via ``json.dumps`` with the default ``allow_nan=True``,
    which would emit a bare token strict JSON readers reject.
    """
    if value is None:
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _non_finite_mask(df, trait_cols):
    """Boolean (rows x traits) mask of ``+inf``/``-inf``.

    ``na_value=np.nan`` is free insurance against a future nullable dtype (``Float64`` with
    ``pd.NA``), on which a bare ``to_numpy(dtype="float64")`` raises — the same call shape
    ``qc_inspect`` already uses.
    """
    return np.isinf(df[trait_cols].to_numpy(dtype="float64", na_value=np.nan))


def trait_sample_size_table(df, trait_cols):
    """Per-trait plotted/missing counts for ``plot_trait_histograms`` (#748).

    ``n_plotted`` is ``pandas`` ``count()`` — non-null values, which **includes** ``+/-inf``,
    matching ``isna``'s convention and the caveat ``qc_inspect`` documents for its own
    missingness fields. ``n_non_finite`` is a SUBSET of it, not an addition, so
    ``n_finite = n_plotted - n_non_finite``. Every flag is computed on ``n_finite``.
    """
    n_rows = len(df)
    counts = df[trait_cols].count()
    inf_counts = _non_finite_mask(df, trait_cols).sum(axis=0)
    table = pd.DataFrame(
        {
            "trait": list(trait_cols),
            "n_plotted": [int(counts[c]) for c in trait_cols],
            "n_non_finite": [int(n) for n in inf_counts],
        }
    )
    table["n_finite"] = table["n_plotted"] - table["n_non_finite"]
    table["n_missing"] = n_rows - table["n_plotted"]
    # A zero-row frame has no missingness to report rather than an undefined fraction.
    table["nan_fraction"] = table["n_missing"] / n_rows if n_rows else 0.0
    return table[TRAIT_TABLE_COLUMNS]


def group_sample_size_table(df, trait_cols, genotype_col):
    """Per-(trait, genotype) counts for ``plot_trait_boxplots`` (#748).

    One row per (resolved trait x observed genotype) cell, including cells with **no** data —
    those are the boxes the delegate draws no tick for at all, and naming them is half the point
    of the disclosure. Rows whose genotype value is null are excluded from every cell, matching
    the delegate's own ``df[[trait, genotype_col]].dropna()`` (``groupby``'s default
    ``dropna=True`` agrees with it cell-for-cell — verified).

    Vectorized end to end: one ``groupby().count()`` plus one grouped ``isinf`` sum. A Python
    loop over the (trait x genotype) grid is prohibitive at cylinder width; only the flagged
    tail is ever materialized by the caller. Measured at 3,000 rows x 846 traits x 60
    genotypes: 5 ms and 4 ms respectively.

    Returns an EMPTY frame (with the full column set) when no genotype group survives — an
    all-null genotype column is reachable today and renders successfully, so this must not raise
    (see the tools' Optional summaries).
    """
    grouper = df[genotype_col]
    counts = df.groupby(grouper, sort=True)[trait_cols].count()
    if counts.empty:
        return pd.DataFrame(columns=GROUP_TABLE_COLUMNS)

    counts.index.name = "genotype"
    counts.columns.name = "trait"
    inf_counts = (
        pd.DataFrame(
            _non_finite_mask(df, trait_cols), columns=trait_cols, index=df.index
        )
        .groupby(grouper, sort=True)
        .sum()
    )
    inf_counts.index.name = "genotype"
    inf_counts.columns.name = "trait"

    table = counts.stack().rename("n_plotted").reset_index()
    table["n_non_finite"] = (
        inf_counts.stack().rename("n_non_finite").reset_index()["n_non_finite"]
    )
    table["n_finite"] = table["n_plotted"] - table["n_non_finite"]
    table["n_rows_in_group"] = table["genotype"].map(grouper.value_counts()).astype(int)
    table["n_missing"] = table["n_rows_in_group"] - table["n_plotted"]
    table["nan_fraction"] = table["n_missing"] / table["n_rows_in_group"]
    # Deterministic order: the committed CSV and every derived list read the same way twice.
    table = table.sort_values(["trait", "genotype"], kind="stable").reset_index(
        drop=True
    )
    return table[GROUP_TABLE_COLUMNS]
