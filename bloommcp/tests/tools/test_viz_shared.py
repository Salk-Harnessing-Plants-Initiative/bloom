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
