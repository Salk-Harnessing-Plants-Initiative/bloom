"""Unit tests for `sections/sleap_roots/analysis/_viz_shared.py`.

This file was `test_viz_tools.py`: C3 golden + delegation coverage for the string-returning
plotting tools, plus tests of the `_viz_shared` helpers they shared. Two changes emptied
that first role. #466 converged `plot_trait_histograms`/`plot_trait_boxplots`/
`plot_correlation_matrix` onto `@as_mcp_tool` and moved their coverage to their own
contract-test files (`test_plot_*_tool.py`); #462 retired the last two string-returning
tools (`plot_heritability_bar`, `plot_variance_decomposition`) into `heritability_analysis`,
whose figures are covered by `test_heritability_analysis_tool.py` against the granular
contract. The `save_plot`/`save_plot_or_plots`/`parse_traits`/`validate_filename` tests
went with the helpers, which had no caller left.

What remains is what `_viz_shared` still exports: `TRAIT_BATCH_THRESHOLD` (pinned against
the live upstream default it mirrors) and `resolve_trait_columns` (direct coverage, not only
indirect via the 3 converged tools' contract tests — #466 review).
"""

from __future__ import annotations

import pandas as pd
import pytest

from bloom_mcp.sections.sleap_roots.analysis import _viz_shared


def test_trait_batch_threshold_matches_heritability_plot_default():
    """#483 follow-up: TRAIT_BATCH_THRESHOLD is set to match
    create_heritability_plot's own internal traits_per_page default (50) "for
    consistency across all plot tools" -- assert that against the live delegate
    signature so a future sleap-roots-analyze bump that changes that default is
    caught here, not silently desynced (the pin is `>=`, open-ended)."""
    import inspect

    from sleap_roots_analyze.visualization import create_heritability_plot

    default = (
        inspect.signature(create_heritability_plot)
        .parameters["traits_per_page"]
        .default
    )
    assert default == _viz_shared.TRAIT_BATCH_THRESHOLD


# ── resolve_trait_columns (#466 review: direct coverage, not just indirect via the 3
# converged tools' own contract tests) ───────────────────────────────────────


def _frame_with_traits():
    """A real ExperimentFrame via FakeReader — resolve_trait_columns takes frame.df/
    frame.trait_cols, not a hand-rolled stub, so this exercises it exactly as the 3
    converged tools do."""
    from bloom_mcp.data_access import FakeReader

    df = pd.DataFrame(
        {
            "Barcode": [f"b{i}" for i in range(6)],
            "geno": ["g1", "g2"] * 3,
            "t1": [float(i) for i in range(6)],
            "t2": [float(2 * i + 1) for i in range(6)],
        }
    )
    reader = FakeReader()
    reader.add_experiment("resolve.csv", df)
    return reader.load_experiment("resolve.csv", version="raw")


def test_resolve_trait_columns_none_returns_all_detected_traits():
    frame = _frame_with_traits()
    assert _viz_shared.resolve_trait_columns(frame, None, "resolve.csv") == list(
        frame.trait_cols
    )


def test_resolve_trait_columns_explicit_subset_is_honored():
    frame = _frame_with_traits()
    assert _viz_shared.resolve_trait_columns(frame, ["t1"], "resolve.csv") == ["t1"]


def test_resolve_trait_columns_empty_list_is_invalid_input():
    from bloom_mcp.contract import BloomMCPError

    frame = _frame_with_traits()
    with pytest.raises(BloomMCPError) as exc:
        _viz_shared.resolve_trait_columns(frame, [], "resolve.csv")
    assert exc.value.code == "invalid_input"


def test_resolve_trait_columns_duplicate_is_invalid_input():
    from bloom_mcp.contract import BloomMCPError

    frame = _frame_with_traits()
    with pytest.raises(BloomMCPError) as exc:
        _viz_shared.resolve_trait_columns(frame, ["t1", "t1"], "resolve.csv")
    assert exc.value.code == "invalid_input"
    assert "t1" in exc.value.message


def test_resolve_trait_columns_unknown_column_is_invalid_input():
    from bloom_mcp.contract import BloomMCPError

    frame = _frame_with_traits()
    with pytest.raises(BloomMCPError) as exc:
        _viz_shared.resolve_trait_columns(frame, ["NoSuchTrait"], "resolve.csv")
    assert exc.value.code == "invalid_input"


def _frame_with_an_all_nan_trait():
    """A detected trait column that is entirely NaN — resolve_trait_columns itself does
    NOT validate variance (existence + numeric dtype only); the all-zero-variance guard
    lives in plot_correlation_matrix alone, since a histogram/boxplot of an all-NaN trait
    is a legitimate (if uninformative) plot, unlike a correlation matrix cell that needs
    variance to mean anything (#466 review round 5: this exact "computed but not
    surfaced" bug class had no direct test against this shared helper)."""
    from bloom_mcp.data_access import FakeReader

    df = pd.DataFrame(
        {
            "Barcode": [f"b{i}" for i in range(6)],
            "geno": ["g1", "g2"] * 3,
            "t1": [float(i) for i in range(6)],
            # float("nan"), not None/[None]*6: the latter infers dtype=object in pandas
            # (not numeric), which would make this column fail the *numeric* check before
            # ever reaching the variance question this test is actually about.
            "all_nan": [float("nan")] * 6,
        }
    )
    reader = FakeReader()
    reader.add_experiment("resolve_nan.csv", df)
    return reader.load_experiment("resolve_nan.csv", version="raw")


def test_resolve_trait_columns_all_nan_trait_is_included_not_dropped_or_rejected():
    frame = _frame_with_an_all_nan_trait()
    assert (
        "all_nan" in frame.trait_cols
    )  # confirms it's genuinely auto-detected as a trait
    resolved = _viz_shared.resolve_trait_columns(frame, None, "resolve_nan.csv")
    assert "all_nan" in resolved


def test_resolve_trait_columns_explicit_all_nan_trait_is_honored():
    frame = _frame_with_an_all_nan_trait()
    assert _viz_shared.resolve_trait_columns(
        frame, ["t1", "all_nan"], "resolve_nan.csv"
    ) == ["t1", "all_nan"]


# ── sample-size disclosure helpers (#748) ────────────────────────────────────


def test_min_plotted_samples_is_owned_not_aliased():
    """#748: the floor is this module's own constant, not an alias for the QC per-trait
    completeness convention. The two answer different questions -- "enough samples to keep a
    trait during cleaning" vs "enough points for a box to be made of data" -- and aliasing
    would let a QC-side retune silently move which boxes these tools flag.

    Deliberately does NOT assert any relationship to _CANONICAL_MIN_SAMPLES_PER_TRAIT:
    independence is the point, and pinning an equality would turn the next legitimate QC
    retune into a failure whose cheapest fix is to re-alias.
    """
    assert _viz_shared.MIN_PLOTTED_SAMPLES == 5
    # Owned = defined here, not re-exported from somewhere else.
    assert "MIN_PLOTTED_SAMPLES" in _viz_shared.__dict__
    # The regression this guards is a future `from _qc_shared import
    # _CANONICAL_MIN_SAMPLES_PER_TRAIT` -- the module already imports _validate_trait_subset
    # from there, so the import path itself is live.
    assert not hasattr(_viz_shared, "_CANONICAL_MIN_SAMPLES_PER_TRAIT")


def test_five_is_the_first_sample_size_whose_quartiles_are_order_statistics():
    """Pins Decision 3's rationale as a property, not a comment: n=5 is the smallest n>1 at
    which Q1, the median and Q3 all land exactly on observations rather than on
    interpolations between them. Below it the drawn box is made of the interpolation rule.
    """
    import numpy as np

    def all_on_order_statistics(n: int) -> bool:
        x = np.arange(1.0, n + 1)
        return all(
            any(abs(q - v) < 1e-12 for v in x) for q in np.percentile(x, [25, 50, 75])
        )

    assert not any(all_on_order_statistics(n) for n in range(2, 5))
    assert all_on_order_statistics(_viz_shared.MIN_PLOTTED_SAMPLES)


def test_caps_are_defined_and_ordered():
    assert _viz_shared.MAX_FLAGGED_REPORTED == 20
    assert _viz_shared.MAX_NOTE_NAMES == 10
    assert _viz_shared.MAX_NOTE_NAMES <= _viz_shared.MAX_FLAGGED_REPORTED


def test_native_coerces_numpy_scalars():
    """np.int64/np.float64 in a run's params raise PydanticSerializationError at the
    manifest write (design.md Decision 9) -- every stamped value goes through this."""
    import numpy as np

    assert type(_viz_shared.native(np.int64(3))) is int
    assert type(_viz_shared.native(np.float64(1.5))) is float
    assert _viz_shared.native(None) is None
    assert _viz_shared.native(float("nan")) is None
    assert _viz_shared.native(float("inf")) is None


def _sample_frame():
    return pd.DataFrame(
        {
            "geno": ["A", "A", "A", "B", "B", None],
            "t_full": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
            "t_gappy": [1.0, None, None, 4.0, None, 6.0],
            "t_inf": [1.0, float("inf"), 3.0, 4.0, 5.0, 6.0],
        }
    )


def test_trait_sample_size_table_counts_and_fractions():
    table = _viz_shared.trait_sample_size_table(
        _sample_frame(), ["t_full", "t_gappy", "t_inf"]
    )
    rows = {r["trait"]: r for r in table.to_dict("records")}
    assert rows["t_full"]["n_plotted"] == 6
    assert rows["t_full"]["n_missing"] == 0
    assert rows["t_gappy"]["n_plotted"] == 3
    assert rows["t_gappy"]["n_missing"] == 3
    assert rows["t_gappy"]["nan_fraction"] == pytest.approx(0.5)
    # count() treats +/-inf as present -- pinned deliberately (design.md Decision 5).
    assert rows["t_inf"]["n_plotted"] == 6
    assert rows["t_inf"]["n_non_finite"] == 1
    assert rows["t_inf"]["n_finite"] == 5


def test_group_sample_size_table_counts_per_trait_per_genotype():
    table = _viz_shared.group_sample_size_table(
        _sample_frame(), ["t_full", "t_gappy", "t_inf"], "geno"
    )
    rows = {(r["trait"], r["genotype"]): r for r in table.to_dict("records")}
    # The null-genotype row is dropped from every box, matching the delegate.
    assert {g for _, g in rows} == {"A", "B"}
    assert rows[("t_full", "A")]["n_plotted"] == 3
    assert rows[("t_full", "A")]["n_rows_in_group"] == 3
    assert rows[("t_gappy", "A")]["n_plotted"] == 1
    assert rows[("t_gappy", "A")]["n_missing"] == 2
    assert rows[("t_gappy", "B")]["n_plotted"] == 1
    assert rows[("t_inf", "A")]["n_plotted"] == 3
    assert rows[("t_inf", "A")]["n_non_finite"] == 1
    assert rows[("t_inf", "A")]["n_finite"] == 2
    # Every (trait, genotype) cell is present, including ones with no data.
    assert len(rows) == 3 * 2


def test_group_sample_size_table_includes_absent_cells_as_zero():
    df = pd.DataFrame({"geno": ["A", "A", "B", "B"], "t": [1.0, 2.0, None, None]})
    rows = {
        (r["trait"], r["genotype"]): r
        for r in _viz_shared.group_sample_size_table(df, ["t"], "geno").to_dict(
            "records"
        )
    }
    assert rows[("t", "B")]["n_plotted"] == 0
    assert rows[("t", "B")]["n_rows_in_group"] == 2
    assert rows[("t", "B")]["nan_fraction"] == pytest.approx(1.0)


def test_group_sample_size_table_on_an_all_null_genotype_column_is_empty():
    """Reachable today: the tool guards only `genotype_col is None`, and the delegate renders
    an all-null genotype column successfully. The table must come back empty rather than
    raising, so the caller can report null summaries instead of crashing (Decision 8).
    """
    df = pd.DataFrame({"geno": [None, None], "t": [1.0, 2.0]})
    table = _viz_shared.group_sample_size_table(df, ["t"], "geno")
    assert list(table.columns) == [
        "trait",
        "genotype",
        "n_rows_in_group",
        "n_plotted",
        "n_finite",
        "n_non_finite",
        "n_missing",
        "nan_fraction",
    ]
    assert table.empty
