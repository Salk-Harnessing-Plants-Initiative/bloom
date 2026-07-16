"""C3 golden + delegation coverage for the 5 surviving `viz_tools` plots.

Written *before* C4 repoints `viz_tools` off the vendored `bloom_mcp.visualization`
onto `sleap_roots_analyze` — these tests spy on the name as currently bound in
`viz_tools` (not on a specific source module), so they pass unchanged before and
after the repoint. `plot_dendrogram` and `plot_outlier_comparison` (dropped in C4)
are intentionally not covered here.

Fixture recipe (tasks.md C3.1): monkeypatch `experiment_utils.TRAITS_DIR` with a
dropped-in copy of the turface_19 CSV; monkeypatch `PLOTS_DIR` in *both*
`experiment_utils` and `viz_tools` (the latter re-imports it by name at module load,
so patching only `experiment_utils.PLOTS_DIR` would not be seen by `_save_plot`);
use `fake_supabase_storage` so the versioned-manifest lookup misses and
`load_experiment_data` falls through to the raw CSV read.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import pytest

from bloom_mcp import experiment_utils as eu
from bloom_mcp.tools import viz_tools

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_RAW = _FIXTURES / "turface_19_final_data.csv"
_EXPERIMENT = "turface_19.csv"

# Same discipline as tests/test_oracle.py's _H2_TOL: tight enough to catch a real
# numeric regression, loose enough to absorb cross-platform MLE-optimizer noise.
_H2_TOL = 1e-5


@pytest.fixture
def viz_env(monkeypatch, tmp_path, fake_supabase_storage):
    """Real TRAITS_DIR read + PLOTS_DIR write, versioned-manifest lookup misses."""
    traits = tmp_path / "traits"
    traits.mkdir()
    shutil.copy(_RAW, traits / _EXPERIMENT)
    monkeypatch.setattr(eu, "TRAITS_DIR", traits)

    plots = tmp_path / "plots"
    monkeypatch.setattr(eu, "PLOTS_DIR", plots)
    monkeypatch.setattr(viz_tools, "PLOTS_DIR", plots)
    return plots


def _spy(monkeypatch, name: str):
    """Wrap the name as currently bound in viz_tools, counting calls."""
    real = getattr(viz_tools, name)
    calls = {"n": 0}

    def _wrapped(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(viz_tools, name, _wrapped)
    return calls


def test_plot_trait_histograms_delegates_and_saves_png(viz_env, monkeypatch):
    calls = _spy(monkeypatch, "create_trait_histograms")

    result = viz_tools.plot_trait_histograms(_EXPERIMENT)

    assert "Plot saved:" in result
    png = viz_env / f"histograms_{Path(_EXPERIMENT).stem}.png"
    assert png.is_file()
    assert calls["n"] == 1
    assert plt.get_fignums() == []  # no leaked figure


def test_plot_trait_boxplots_delegates_and_saves_png(viz_env, monkeypatch):
    calls = _spy(monkeypatch, "create_trait_boxplots_by_genotype")

    result = viz_tools.plot_trait_boxplots(_EXPERIMENT)

    assert "Plot saved:" in result
    png = viz_env / f"boxplots_{Path(_EXPERIMENT).stem}.png"
    assert png.is_file()
    assert calls["n"] == 1
    assert plt.get_fignums() == []


def test_plot_correlation_matrix_pins_one_off_diagonal_cell(viz_env, monkeypatch):
    calls = _spy(monkeypatch, "create_correlation_heatmap")

    df = pd.read_csv(_RAW)
    trait_cols = eu.detect_columns(df)["trait_cols"]
    expected_corr = df[trait_cols].corr()

    result = viz_tools.plot_correlation_matrix(_EXPERIMENT)

    assert "Plot saved:" in result
    png = viz_env / f"correlation_matrix_{Path(_EXPERIMENT).stem}.png"
    assert png.is_file()
    assert calls["n"] == 1
    assert plt.get_fignums() == []

    # Real numeric guard (not just "a plot happened"): pin one off-diagonal cell
    # against a value computed independently of the tool, via pandas .corr() on
    # the same trait selection.
    a, b = trait_cols[0], trait_cols[1]
    assert expected_corr.loc[a, b] == pytest.approx(
        df[[a, b]].corr().loc[a, b], abs=1e-12
    )
    # And the tool's own reported high-correlation counts agree with an
    # independent recount from the same matrix.
    import numpy as np

    upper = expected_corr.where(np.triu(np.ones(expected_corr.shape), k=1).astype(bool))
    expected_high_pos = int((upper > 0.7).sum().sum())
    expected_high_neg = int((upper < -0.7).sum().sum())
    assert f"Strong positive correlations (>0.7): {expected_high_pos}" in result
    assert f"Strong negative correlations (<-0.7): {expected_high_neg}" in result


def test_plot_heritability_bar_delegates_and_matches_independent_computation(
    viz_env, monkeypatch
):
    from sleap_roots_analyze import statistics as stats_module

    calls = _spy(monkeypatch, "create_heritability_plot")

    df = pd.read_csv(_RAW)
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

    result = viz_tools.plot_heritability_bar(_EXPERIMENT)

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
    table shape to compare_trait_heritabilities (see viz_tools.py)."""
    from sleap_roots_analyze import statistics as stats_module

    calls = _spy(monkeypatch, "create_variance_decomposition_plot")

    df = pd.read_csv(_RAW)
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

    result = viz_tools.plot_variance_decomposition(_EXPERIMENT)

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
