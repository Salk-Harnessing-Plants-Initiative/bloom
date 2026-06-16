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

import bloom_mcp.clustering as shipped_clustering
import bloom_mcp.cross_experiment_correlations as shipped_corr
import bloom_mcp.pca as shipped_pca
from sleap_roots_analyze.pca import perform_pca_analysis as library_pca

_FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Cumulative explained variance is a normalized ratio computed via full SVD (8
# features forces sklearn's deterministic 'full' solver, no randomized path), so
# it is stable to well under 1e-6 across BLAS backends; a real numpy-2 numeric
# regression would shift it by far more.
_VAR_TOL = 1e-6
_GENOTYPE_COL = "Genotype"


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
