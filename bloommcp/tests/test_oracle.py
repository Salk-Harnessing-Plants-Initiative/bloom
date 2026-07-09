"""Cross-tier + shipped-code numeric oracles on the #120 turface_19 fixture.

The input table and golden values are vendored from the talmolab/sleap-roots-analyze
#120 / PR #146 fixtures (see ``tests/fixtures/README.md``); they are NOT re-derived
from the code under test, so a numeric drift would fail these tests. Two layers:

* **Cross-tier** — the external ``sleap_roots_analyze`` reproduces the recorded
  #120 PCA (proves it is a safe future cutover target).
* **Shipped-code under numpy 2** — the vendored ``bloom_mcp`` analysis the server
  actually runs reproduces the same PCA, and produces deterministic clustering /
  correlation numerics. This guards the numpy >=1.24 -> >=2.3.2 major-version jump
  that ``sleap-roots-analyze`` pulls in (B1): "doesn't crash" is not "unchanged".

All assertions are explicit with stated tolerances — no auto-generated snapshot.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from sklearn.manifold import trustworthiness
from sklearn.preprocessing import StandardScaler

import bloom_mcp.clustering as shipped_clustering
import bloom_mcp.cross_experiment_correlations as shipped_corr
import bloom_mcp.pca as shipped_pca
from sleap_roots_analyze.pca import perform_pca_analysis as library_pca
from sleap_roots_analyze.statistics import (
    calculate_heritability_estimates as library_heritability,
)
from sleap_roots_analyze.umap import perform_umap_analysis as library_umap

_FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Cumulative explained variance is a normalized ratio computed via full SVD (8
# features forces sklearn's deterministic 'full' solver, no randomized path), so
# it is stable to well under 1e-6 across BLAS backends; a real numpy-2 numeric
# regression would shift it by far more.
_VAR_TOL = 1e-6
_GENOTYPE_COL = "Genotype"
_REPLICATE_COL = "Replicate"

# Mean broad-sense heritability over the 8 trait_cols, via the statsmodels MixedLM
# path. The relative tolerance absorbs cross-platform MLE-optimizer noise but is far
# tighter than any real numeric regression; the method label and the (discrete)
# count of high-H² traits are BLAS/optimizer-robust drift guards.
_H2_TOL = 1e-5


@pytest.fixture(scope="module")
def turface_19():
    df = pd.read_csv(_FIXTURES / "turface_19_final_data.csv")
    golden = json.loads((_FIXTURES / "turface_19_pca_golden.json").read_text())
    return df, golden


def _cumulative_variance_at_cut(result: dict, n: int) -> float:
    return float(result["cumulative_variance_ratio"][n - 1])


# ── Cross-tier oracle: the external library matches the recorded #120 PCA ────


def test_external_library_pca_matches_recorded_oracle(turface_19):
    df, golden = turface_19
    result = library_pca(df[golden["trait_cols"]], explained_variance_threshold=0.95)
    assert result["n_components_selected"] == golden["n_pca_components"]
    assert _cumulative_variance_at_cut(
        result, golden["n_pca_components"]
    ) == pytest.approx(golden["pca_explained_variance"], abs=_VAR_TOL)


# ── Shipped code reproduces the same PCA under numpy 2 (B1) ──────────────────


def test_shipped_pca_matches_recorded_oracle(turface_19):
    """The vendored bloom_mcp.pca the server runs reproduces #120's PCA."""
    df, golden = turface_19
    result = shipped_pca.perform_pca_analysis(
        df[golden["trait_cols"]], explained_variance_threshold=0.95
    )
    assert result["n_components_selected"] == golden["n_pca_components"]
    assert _cumulative_variance_at_cut(
        result, golden["n_pca_components"]
    ) == pytest.approx(golden["pca_explained_variance"], abs=_VAR_TOL)


def test_shipped_kmeans_is_deterministic_under_numpy2(turface_19):
    """Fixed-seed k-means gives stable, reproducible labels + inertia."""
    df, golden = turface_19
    data = df[golden["trait_cols"]]

    first = shipped_clustering.perform_kmeans_clustering(
        data, n_clusters=3, random_state=42
    )
    second = shipped_clustering.perform_kmeans_clustering(
        data, n_clusters=3, random_state=42
    )

    # Discrete cluster sizes are BLAS-robust and the strongest stability signal.
    sizes = sorted(first["cluster_sizes"])
    assert sizes == [28, 40, 85]
    assert sorted(second["cluster_sizes"]) == sizes
    # Inertia: identical within-process, and pinned to the recorded value with a
    # relative tolerance that absorbs cross-BLAS noise but catches real drift.
    assert first["inertia"] == pytest.approx(second["inertia"], rel=1e-9)
    assert first["inertia"] == pytest.approx(333.4642, rel=1e-3)


def test_shipped_correlations_reproduce_recorded_offdiagonal(turface_19):
    """A pinned off-diagonal Pearson coefficient — a real numpy-2 drift guard.

    Self-correlations (corr(x, x) == 1) are a mathematical identity and would
    pass even if every off-diagonal value were wrong, so they are not a drift
    guard. Instead pin a recorded cross-trait coefficient computed by the
    shipped correlation path over the 19 turface_19 genotype means.
    """
    df, golden = turface_19
    traits = golden["trait_cols"]
    means = df.groupby(_GENOTYPE_COL)[traits].mean()

    corr = shipped_corr.calculate_cross_experiment_correlations(
        means, means, traits, traits, min_samples=3
    )
    pair = corr[
        (corr["exp1_trait"] == "Surface Area (mm²)")
        & (corr["exp2_trait"] == "Volume (mm³)")
    ]
    assert len(pair) == 1
    # Recorded from the shipped path; rel tolerance absorbs BLAS noise but
    # catches a real numeric regression.
    assert pair.iloc[0]["correlation"] == pytest.approx(0.9789420001158863, rel=1e-6)
    assert int(pair.iloc[0]["n_samples"]) == len(means)


# ── Delegated paths (heritability, UMAP) reproduce the recorded oracle ───────
#
# The shipped heritability and UMAP tools delegate to ``sleap_roots_analyze`` (the
# vendored ``trait_statistics.py`` / ``umap_embedding.py`` were byte-identical copies
# and were removed). These lock the delegation target's behavior to the recorded #120
# golden, so a future library bump that moves the numbers fails here.


# Keys the shipped `viz_tools` wrappers consume from each per-trait heritability
# result. `plot_variance_decomposition` reads var_genetic/var_residual; the bar/table
# tools read heritability. A library rename/drop of any of these would otherwise be
# silently defaulted to 0 by the wrappers' `.get(key, 0)`, shipping a wrong variance
# decomposition with no failure — so the delegation boundary asserts they exist.
_WRAPPER_CONSUMED_TRAIT_KEYS = ("heritability", "var_genetic", "var_residual")


def test_external_library_heritability_matches_recorded_oracle(turface_19):
    """The delegated heritability path reproduces the recorded #120 mean H².

    NOTE: ``heritability_mean`` is a *characterization snapshot* of 0.1.0a2 on this
    fixture (see the golden's ``_heritability_source``), not an independently validated
    value — this asserts no-drift from the recorded library output, not scientific
    correctness. ``heritability_method`` and the discrete high-H² count are the
    BLAS/optimizer-robust drift guards.
    """
    df, golden = turface_19
    results = library_heritability(
        df,
        golden["trait_cols"],
        genotype_col=_GENOTYPE_COL,
        replicate_col=_REPLICATE_COL,
    )
    h2 = [
        float(results[t]["heritability"])
        for t in golden["trait_cols"]
        if "heritability" in results.get(t, {})
    ]
    mean_h2 = sum(h2) / len(h2)
    assert mean_h2 == pytest.approx(golden["heritability_mean"], rel=_H2_TOL)
    assert (
        results["__calculation_metadata__"]["method_used_for_all_traits"]
        == golden["heritability_method"]
    )
    # Discrete count of high-H² traits — optimizer-robust drift guard.
    assert sum(1 for v in h2 if v >= 0.5) == golden["heritability_n_above_0.5"]


def test_delegated_heritability_returns_wrapper_consumed_keys(turface_19):
    """Delegation-boundary contract: every per-trait result the wrappers plot carries
    the keys they read (heritability, var_genetic, var_residual), non-defaulted.

    Guards I3 — `viz_tools.plot_variance_decomposition` does `r.get("var_genetic", 0)`,
    so a future library key-rename would silently plot 0 variance instead of erroring.
    This pins the contract at the source so the rename fails CI here.
    """
    df, golden = turface_19
    results = library_heritability(
        df,
        golden["trait_cols"],
        genotype_col=_GENOTYPE_COL,
        replicate_col=_REPLICATE_COL,
    )
    plotted = [
        t for t in golden["trait_cols"] if "heritability" in results.get(t, {})
    ]
    assert plotted, "fixture should yield at least one plottable trait"
    for trait in plotted:
        r = results[trait]
        for key in _WRAPPER_CONSUMED_TRAIT_KEYS:
            assert key in r, (
                f"trait {trait!r} missing wrapper-consumed key {key!r}: "
                f"library contract changed — viz_tools would silently default it to 0"
            )
            assert isinstance(r[key], (int, float))


def test_delegated_heritability_degrades_on_edge_cases():
    """I4 — exercise the non-balanced branches the turface_19 fixture never hits.

    The deleted ``trait_statistics.py`` had distinct branches (no_variance,
    mixed_model fallback, small-N guards). The balanced golden never reaches them, so a
    future bump could change them silently. This pins graceful degradation on a
    zero-variance + small-N frame: no raise, model_type marker present, and the
    wrapper-consumed keys still present (so the variance wrapper stays safe there too).
    """
    edge = pd.DataFrame(
        {
            _GENOTYPE_COL: ["A", "A", "B", "B"],
            _REPLICATE_COL: [1, 2, 1, 2],
            "flat": [5.0, 5.0, 5.0, 5.0],  # zero variance
            "varied": [1.0, 2.0, 9.0, 10.0],
        }
    )
    results = library_heritability(
        edge,
        ["flat", "varied"],
        genotype_col=_GENOTYPE_COL,
        replicate_col=_REPLICATE_COL,
    )
    # Zero-variance trait degrades to the no_variance branch, not a crash.
    assert results["flat"]["model_type"] == "no_variance"
    assert results["flat"]["heritability"] == 0.0
    # Both traits still carry the keys the wrappers read.
    for trait in ("flat", "varied"):
        for key in _WRAPPER_CONSUMED_TRAIT_KEYS:
            assert key in results[trait]


def _umap_trustworthiness(df, trait_cols, embedding, n_neighbors=15) -> float:
    """Neighbor-preservation of an embedding w.r.t. the standardized input traits.

    A structural invariant that survives cross-OS coordinate instability (unlike raw
    coords) but collapses for a wrong-parameter embedding — see the golden's
    ``_umap_source``.
    """
    standardized = StandardScaler().fit_transform(df[trait_cols].to_numpy())
    return float(trustworthiness(standardized, embedding, n_neighbors=n_neighbors))


def test_external_library_umap_is_deterministic_and_structural(turface_19):
    """Fixed-seed UMAP is reproducible AND preserves local structure.

    I1 — shape + within-process determinism alone is trivially true regardless of
    correctness; a delegation with wrong n_neighbors/min_dist/init that produced a
    same-shape deterministic embedding would pass. So additionally:
      * assert the library echoes the requested n_neighbors/min_dist (catches the
        silent default-swap that dimred.py masks via `.get(default)`); and
      * assert a structural trustworthiness invariant against the recorded snapshot,
        with a floor a wrong-parameter embedding cannot clear.
    """
    df, golden = turface_19
    trait_cols = golden["trait_cols"]
    first = library_umap(df, feature_cols=trait_cols, random_state=42)
    second = library_umap(df, feature_cols=trait_cols, random_state=42)

    # Shape + within-process determinism (necessary, not sufficient).
    assert first["embedding"].shape == (len(df), 2)
    assert (first["embedding"] == second["embedding"]).all()

    # Parameter echo — a silent default-swap in the delegation surfaces here.
    assert first["n_neighbors"] == 15
    assert first["min_dist"] == pytest.approx(0.1)

    # Structural invariant: neighbor-preservation must stay near the recorded snapshot
    # and clear the floor (a wrong-parameter embedding drops far below it).
    t = _umap_trustworthiness(df, trait_cols, first["embedding"])
    assert t >= golden["umap_trustworthiness_floor"]
    assert t == pytest.approx(golden["umap_trustworthiness"], abs=0.05)


def test_umap_trustworthiness_floor_rejects_wrong_parameters(turface_19):
    """The structural floor actually discriminates: a wrong n_neighbors collapses it.

    Without this, a reviewer can't tell the trustworthiness gate from a tautology.
    """
    df, golden = turface_19
    trait_cols = golden["trait_cols"]
    wrong = library_umap(df, feature_cols=trait_cols, random_state=42, n_neighbors=2)
    t_wrong = _umap_trustworthiness(df, trait_cols, wrong["embedding"])
    assert t_wrong < golden["umap_trustworthiness_floor"]
