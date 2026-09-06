"""C3 golden + delegation coverage for the 2 remaining bare-`mcp.tool()` plotting tools.

Each tool now lives in its own file under `sections/sleap_roots/analysis/`
(moved by the Phase-2 sections migration, devendor-bloommcp-analysis) — these
tests spy on the delegate name as bound in each tool's own module.
`plot_dendrogram` and `plot_outlier_comparison` (dropped in C4) are
intentionally not covered here.

`plot_trait_histograms`, `plot_trait_boxplots`, and `plot_correlation_matrix` converged onto
`@as_mcp_tool` (#466) and moved to their own contract-test files
(`test_plot_trait_histograms_tool.py`, `test_plot_trait_boxplots_tool.py`,
`test_plot_correlation_matrix_tool.py`), which use the `FakeReader`/`FakeResultStore` harness
instead of this file's `viz_env`/`PLOTS_DIR` fixture. `plot_heritability_bar` and
`plot_variance_decomposition` remain bare `mcp.tool()` functions (retiring into
`heritability_analysis` per #462) and keep their string-based assertions here unchanged.

Fixture recipe (tasks.md C3.1): monkeypatch `experiment_utils.TRAITS_DIR` with a
dropped-in copy of the turface_19 CSV; monkeypatch `PLOTS_DIR` in `_viz_shared`
(the one place both remaining tools re-import it from, so a single patch covers
them); use `fake_supabase_storage` so the versioned-manifest lookup misses and
`load_experiment_data` falls through to the raw CSV read. The `viz_env` fixture
itself now lives in `conftest.py` (#713), shared with `test_viz_snapshot.py`.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import pytest

from bloom_mcp import experiment_utils as eu
from bloom_mcp.sections.sleap_roots.analysis import (
    _viz_shared,
    plot_heritability_bar as plot_heritability_bar_mod,
    plot_variance_decomposition as plot_variance_decomposition_mod,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_RAW = _FIXTURES / "turface_19_final_data.csv"
_EXPERIMENT = "turface_19.csv"

# Same discipline as tests/test_oracle.py's _H2_TOL: tight enough to catch a real
# numeric regression, loose enough to absorb cross-platform MLE-optimizer noise.
_H2_TOL = 1e-5


def _spy(monkeypatch, module, name: str):
    """Wrap the name as currently bound in `module`, counting calls."""
    real = getattr(module, name)
    calls = {"n": 0}

    def _wrapped(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, _wrapped)
    return calls


# ── save_plot_or_plots (#483 follow-up) ──────────────────────────────────────
#
# Direct, fast, unmarked coverage of the exact bug PR #507 found and fixed
# (`create_heritability_plot` returning `list[Figure]` past its pagination
# threshold crashed `save_plot`'s unconditional `fig.savefig()`) -- this was
# previously only exercised by the live_smoke_slow-only cylinder smoke tests,
# which never run in per-PR CI.


def test_save_plot_or_plots_single_figure_behaves_like_save_plot(viz_env):
    fig = plt.figure()
    url = _viz_shared.save_plot_or_plots(fig, "single.png")
    assert url.endswith("/single.png")
    assert (viz_env / "single.png").is_file()
    assert plt.get_fignums() == []  # closed, not leaked


def test_save_plot_or_plots_list_saves_each_page_and_summarizes(viz_env):
    figs = [plt.figure(), plt.figure(), plt.figure()]
    url = _viz_shared.save_plot_or_plots(figs, "multi.png")
    assert url.startswith("3 pages: ")
    for i in (1, 2, 3):
        assert (viz_env / f"multi_page{i}.png").is_file()
        assert f"multi_page{i}.png" in url
    assert plt.get_fignums() == []  # all three closed, not leaked


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


def test_plot_heritability_bar_delegates_and_matches_independent_computation(
    viz_env, monkeypatch
):
    from sleap_roots_analyze import statistics as stats_module

    calls = _spy(monkeypatch, plot_heritability_bar_mod, "create_heritability_plot")

    df = pd.read_csv(_RAW, encoding="utf-8")
    config = eu.detect_columns(df)
    trait_cols = config["trait_cols"]
    expected = stats_module.calculate_heritability_estimates(
        df,
        trait_cols,
        genotype_col=config["genotype_col"],
        replicate_col=config["replicate_col"],
    )
    assert "error" not in expected, expected.get("error")
    expected_above = sum(
        1
        for t in trait_cols
        if "heritability" in expected.get(t, {}) and expected[t]["heritability"] >= 0.5
    )

    result = plot_heritability_bar_mod.plot_heritability_bar(_EXPERIMENT)

    assert "Plot saved:" in result
    png = viz_env / f"heritability_{Path(_EXPERIMENT).stem}.png"
    assert png.is_file()
    assert calls["n"] == 1
    assert plt.get_fignums() == []
    assert f"{expected_above} above H2 >= 0.5" in result


def test_plot_variance_decomposition_delegates_and_matches_independent_computation(
    viz_env, monkeypatch
):
    """Regression test for a real pre-existing bug: plot_variance_decomposition's
    hand-rolled comparison_df used the wrong column name ("H2" instead of
    "heritability") and omitted columns create_variance_decomposition_plot reads
    (e.g. n_observations) — every call raised a KeyError. Fixed by delegating the
    table shape to compare_trait_heritabilities (see plot_variance_decomposition.py)."""
    from sleap_roots_analyze import statistics as stats_module

    calls = _spy(
        monkeypatch,
        plot_variance_decomposition_mod,
        "create_variance_decomposition_plot",
    )

    df = pd.read_csv(_RAW, encoding="utf-8")
    config = eu.detect_columns(df)
    trait_cols = config["trait_cols"]
    genotype_col, replicate_col = config["genotype_col"], config["replicate_col"]
    expected_h2 = stats_module.calculate_heritability_estimates(
        df, trait_cols, genotype_col=genotype_col, replicate_col=replicate_col
    )
    assert "error" not in expected_h2, expected_h2.get("error")
    expected_comparison = stats_module.compare_trait_heritabilities(
        df,
        trait_cols,
        expected_h2,
        genotype_col=genotype_col,
        replicate_col=replicate_col,
    )
    expected_scored = expected_comparison[expected_comparison["heritability"].notna()]

    result = plot_variance_decomposition_mod.plot_variance_decomposition(_EXPERIMENT)

    assert "Plot saved:" in result
    png = viz_env / f"variance_decomposition_{Path(_EXPERIMENT).stem}.png"
    assert png.is_file()
    assert calls["n"] == 1
    assert plt.get_fignums() == []
    assert f"{len(expected_scored)} traits plotted" in result
    # Every scored trait must carry a finite var_genetic/var_residual — the tool's
    # own guard refuses to plot a zero-filled decomposition otherwise.
    assert not expected_scored["var_genetic"].isna().any()
    assert not expected_scored["var_residual"].isna().any()


# ── Phase 3 / P3.3: path-safety + no-raw-exception-leak stopgap ─────────────

_TOOLS = [
    (plot_heritability_bar_mod, "plot_heritability_bar", "create_heritability_plot"),
    (
        plot_variance_decomposition_mod,
        "plot_variance_decomposition",
        "create_variance_decomposition_plot",
    ),
]
_TOOL_IDS = [name for _module, name, _delegate in _TOOLS]


@pytest.mark.parametrize("module,fn_name,delegate_name", _TOOLS, ids=_TOOL_IDS)
def test_rejects_unsafe_filename_before_any_read(
    module, fn_name, delegate_name, viz_env, monkeypatch, tmp_path
):
    """A traversal/absolute filename must be rejected before any file is read —
    not merely produce an error string after reading. Plant a secret file OUTSIDE
    TRAITS_DIR and prove its content never reaches the tool's output and the
    delegate is never called."""
    secret = tmp_path / "secret.csv"
    secret.write_text("SECRET_MARKER_0xdeadbeef\nGenotype,Rep,t1\na,1,1.0\n")
    fn = getattr(module, fn_name)
    calls = _spy(monkeypatch, module, delegate_name)

    for unsafe_name in (
        "../secret.csv",
        "..\\secret.csv",
        str(secret),
        "/etc/passwd",
    ):
        result = fn(unsafe_name)
        assert "bare experiment identifier" in result
        assert "SECRET_MARKER_0xdeadbeef" not in result

    assert calls["n"] == 0


@pytest.mark.parametrize("module,fn_name,_delegate_name", _TOOLS, ids=_TOOL_IDS)
def test_valid_missing_filename_still_returns_not_found(
    module, fn_name, _delegate_name, viz_env
):
    """Regression guard: the new safety guard must not introduce a false-positive
    rejection on the ordinary 'file not found' path — only unsafe names change
    behavior."""
    fn = getattr(module, fn_name)
    result = fn("does_not_exist.csv")
    assert "bare experiment identifier" not in result
    assert "not found" in result


@pytest.mark.parametrize("module,fn_name,delegate_name", _TOOLS, ids=_TOOL_IDS)
def test_internal_failure_does_not_leak_raw_exception_text(
    module, fn_name, delegate_name, viz_env, monkeypatch
):
    """An unexpected delegate failure must return a sanitized message, never the
    raw exception text (which could carry internal paths or backend details)."""

    def _boom(*_args, **_kwargs):
        raise RuntimeError("internal detail: /secret/backend/path token=abc123")

    monkeypatch.setattr(module, delegate_name, _boom)
    fn = getattr(module, fn_name)

    result = fn(_EXPERIMENT)

    assert "/secret/backend/path" not in result
    assert "token=abc123" not in result
    assert plt.get_fignums() == []
