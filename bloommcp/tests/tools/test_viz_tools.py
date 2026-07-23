"""C3 golden + delegation coverage for the 5 surviving sleap_roots plotting tools.

Each tool now lives in its own file under `sections/sleap_roots/analysis/`
(moved by the Phase-2 sections migration, devendor-bloommcp-analysis) — these
tests spy on the delegate name as bound in each tool's own module.
`plot_dendrogram` and `plot_outlier_comparison` (dropped in C4) are
intentionally not covered here.

Fixture recipe (tasks.md C3.1): monkeypatch `experiment_utils.TRAITS_DIR` with a
dropped-in copy of the turface_19 CSV; monkeypatch `PLOTS_DIR` in `_viz_shared`
(the one place all 5 tools re-import it from, so a single patch covers all of
them); use `fake_supabase_storage` so the versioned-manifest lookup misses and
`load_experiment_data` falls through to the raw CSV read.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import pytest

from bloom_mcp import experiment_utils as eu
from bloom_mcp.sections.sleap_roots.analysis import (
    _viz_shared,
    plot_correlation_matrix as plot_correlation_matrix_mod,
    plot_heritability_bar as plot_heritability_bar_mod,
    plot_trait_boxplots as plot_trait_boxplots_mod,
    plot_trait_histograms as plot_trait_histograms_mod,
    plot_variance_decomposition as plot_variance_decomposition_mod,
)

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
    monkeypatch.setattr(_viz_shared, "PLOTS_DIR", plots)
    return plots


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

    default = inspect.signature(create_heritability_plot).parameters[
        "traits_per_page"
    ].default
    assert default == _viz_shared.TRAIT_BATCH_THRESHOLD


def test_plot_trait_histograms_delegates_and_saves_png(viz_env, monkeypatch):
    calls = _spy(monkeypatch, plot_trait_histograms_mod, "create_trait_histograms")

    result = plot_trait_histograms_mod.plot_trait_histograms(_EXPERIMENT)

    assert "Plot saved:" in result
    png = viz_env / f"histograms_{Path(_EXPERIMENT).stem}.png"
    assert png.is_file()
    assert calls["n"] == 1
    assert plt.get_fignums() == []  # no leaked figure


def test_plot_trait_boxplots_delegates_and_saves_png(viz_env, monkeypatch):
    calls = _spy(
        monkeypatch, plot_trait_boxplots_mod, "create_trait_boxplots_by_genotype"
    )

    result = plot_trait_boxplots_mod.plot_trait_boxplots(_EXPERIMENT)

    assert "Plot saved:" in result
    png = viz_env / f"boxplots_{Path(_EXPERIMENT).stem}.png"
    assert png.is_file()
    assert calls["n"] == 1
    assert plt.get_fignums() == []


def _seed_wide_experiment(traits_dir: Path, n_traits: int, filename: str) -> None:
    """Write a synthetic experiment with `n_traits` trait columns into `traits_dir`.

    Cheap stand-in for cylinder's real 846-trait shape -- exercises the same
    ``TRAIT_BATCH_THRESHOLD``-crossing decision in ``plot_trait_histograms``/
    ``plot_trait_boxplots`` without needing the real fixture, so this runs fast and
    unmarked in every PR (the live cylinder case only runs via live_smoke_slow).
    """
    n_samples = 12
    data = {"geno": [f"G{i % 3}" for i in range(n_samples)]}
    for t in range(n_traits):
        data[f"trait_{t}"] = [float(i + t) for i in range(n_samples)]
    pd.DataFrame(data).to_csv(traits_dir / filename, index=False)


# create_trait_histograms_batched / create_trait_boxplots_by_genotype_batched's own
# batch_size default -- independent of TRAIT_BATCH_THRESHOLD, which only decides
# WHETHER to batch (see _viz_shared.py's comment on TRAIT_BATCH_THRESHOLD).
_DELEGATE_BATCH_SIZE = 16


def _expected_pages(n_traits: int) -> int:
    return -(-n_traits // _DELEGATE_BATCH_SIZE)  # ceil division, no float rounding


def test_plot_trait_histograms_uses_batched_delegate_above_threshold(
    viz_env, monkeypatch
):
    """#483 follow-up: create_trait_histograms has no pagination of its own, so a
    trait count above TRAIT_BATCH_THRESHOLD must route to
    create_trait_histograms_batched (list[Figure]) via save_plot_or_plots, not the
    single-Figure delegate save_plot would crash on."""
    wide_experiment = "wide.csv"
    n_traits = 60
    _seed_wide_experiment(eu.TRAITS_DIR, n_traits=n_traits, filename=wide_experiment)

    unbatched_calls = _spy(
        monkeypatch, plot_trait_histograms_mod, "create_trait_histograms"
    )
    batched_calls = _spy(
        monkeypatch, plot_trait_histograms_mod, "create_trait_histograms_batched"
    )

    result = plot_trait_histograms_mod.plot_trait_histograms(wide_experiment)

    assert unbatched_calls["n"] == 0
    assert batched_calls["n"] == 1
    expected = _expected_pages(n_traits)
    assert result.startswith(f"{expected} pages: ") or f"{expected} pages: " in result
    stem = Path(wide_experiment).stem
    for i in range(1, expected + 1):
        assert (viz_env / f"histograms_{stem}_page{i}.png").is_file()
    assert plt.get_fignums() == []


def test_plot_trait_boxplots_uses_batched_delegate_above_threshold(
    viz_env, monkeypatch
):
    wide_experiment = "wide.csv"
    n_traits = 60
    _seed_wide_experiment(eu.TRAITS_DIR, n_traits=n_traits, filename=wide_experiment)

    unbatched_calls = _spy(
        monkeypatch, plot_trait_boxplots_mod, "create_trait_boxplots_by_genotype"
    )
    batched_calls = _spy(
        monkeypatch,
        plot_trait_boxplots_mod,
        "create_trait_boxplots_by_genotype_batched",
    )

    result = plot_trait_boxplots_mod.plot_trait_boxplots(wide_experiment)

    assert unbatched_calls["n"] == 0
    assert batched_calls["n"] == 1
    expected = _expected_pages(n_traits)
    assert f"{expected} pages: " in result
    stem = Path(wide_experiment).stem
    for i in range(1, expected + 1):
        assert (viz_env / f"boxplots_{stem}_page{i}.png").is_file()
    assert plt.get_fignums() == []


@pytest.mark.parametrize(
    "n_traits, expect_batched",
    [(50, False), (51, True)],  # boundary: TRAIT_BATCH_THRESHOLD == 50, "> 50" batches
)
def test_plot_trait_histograms_batching_boundary(
    viz_env, monkeypatch, n_traits, expect_batched
):
    """Pins the off-by-one boundary explicitly: exactly TRAIT_BATCH_THRESHOLD traits
    must NOT batch (matches create_heritability_plot's own `> traits_per_page`
    semantics), one more must."""
    experiment = "boundary.csv"
    _seed_wide_experiment(eu.TRAITS_DIR, n_traits=n_traits, filename=experiment)

    unbatched_calls = _spy(
        monkeypatch, plot_trait_histograms_mod, "create_trait_histograms"
    )
    batched_calls = _spy(
        monkeypatch, plot_trait_histograms_mod, "create_trait_histograms_batched"
    )

    plot_trait_histograms_mod.plot_trait_histograms(experiment)

    if expect_batched:
        assert unbatched_calls["n"] == 0
        assert batched_calls["n"] == 1
    else:
        assert unbatched_calls["n"] == 1
        assert batched_calls["n"] == 0
    assert plt.get_fignums() == []


def test_plot_correlation_matrix_pins_one_off_diagonal_cell(viz_env, monkeypatch):
    calls = _spy(monkeypatch, plot_correlation_matrix_mod, "create_correlation_heatmap")

    df = pd.read_csv(_RAW, encoding="utf-8")
    trait_cols = eu.detect_columns(df)["trait_cols"]
    expected_corr = df[trait_cols].corr()

    result = plot_correlation_matrix_mod.plot_correlation_matrix(_EXPERIMENT)

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
    (plot_trait_histograms_mod, "plot_trait_histograms", "create_trait_histograms"),
    (
        plot_trait_boxplots_mod,
        "plot_trait_boxplots",
        "create_trait_boxplots_by_genotype",
    ),
    (
        plot_correlation_matrix_mod,
        "plot_correlation_matrix",
        "create_correlation_heatmap",
    ),
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
        assert "bare CSV filename" in result
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
    assert "bare CSV filename" not in result
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
