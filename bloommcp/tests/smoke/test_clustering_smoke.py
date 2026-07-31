"""Live smoke: ``clustering`` (kmeans/gmm/hierarchical) through the real running dev
stack (#483).

Real MCP-transport call against both oracle fixtures. ``clustering`` requires a
cleaned version (``require_clean=True``), so this calls ``qc_clean`` first.
``gmm`` on cylinder is additionally marked ``live_smoke_slow``: a full covariance
matrix per component over ~588 traits vs 123 samples is wildly underdetermined and
prone to EM non-convergence -- see ``cylinder_clustering_golden.json``'s ``gmm._note``.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.live_smoke


def _cluster(call_tool, db_experiment_id: str, **method_kwargs) -> dict:
    call_tool("sleap_roots_qc_clean", {"experiment": db_experiment_id})
    result = call_tool(
        "sleap_roots_clustering",
        {"experiment": db_experiment_id, **method_kwargs},
    )
    assert result["experiment"] == db_experiment_id
    assert result["n_clusters"] >= 2
    assert result["run_ref"]
    return result


def test_kmeans_clustering_smoke(call_tool, db_experiment_id: str) -> None:
    result = _cluster(
        call_tool, db_experiment_id, method="kmeans", n_clusters=3, seed=42
    )
    assert result["method"] == "kmeans"
    assert result["inertia"] is not None


def test_hierarchical_clustering_smoke(call_tool, db_experiment_id: str) -> None:
    result = _cluster(call_tool, db_experiment_id, method="hierarchical", n_clusters=3)
    assert result["method"] == "hierarchical"
    assert result["linkage_method"] == "ward"


@pytest.mark.parametrize(
    "fixture_name",
    ["turface_19", pytest.param("cylinder", marks=pytest.mark.live_smoke_slow)],
)
def test_gmm_clustering_smoke(call_tool, db_experiment_id: str) -> None:
    result = _cluster(
        call_tool,
        db_experiment_id,
        method="gmm",
        n_components=3,
        covariance_type="full",
        seed=42,
    )
    assert result["method"] == "gmm"
    # Do not assert `converged` truthy -- on cylinder EM may trivially "converge" to
    # its k-means initialization without meaningfully fitting a full covariance at
    # this trait count (see cylinder_clustering_golden.json's gmm._note). This smoke
    # test only proves the real MCP round-trip works, not that the fit is meaningful.
    assert "converged" in result
