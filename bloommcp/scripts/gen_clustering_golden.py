"""Regenerate ``tests/fixtures/turface_19_clustering_golden.json`` (Tier 5 / #309).

A **characterization** snapshot, NOT an independently recorded oracle. Every metric literal
in the fixture is emitted here from ``sleap_roots_analyze.perform_*`` on the pinned analyze
version, so the fixture is regenerable and no number is transcribed by hand. The clustering
oracle proper is *determinism* (same seed -> identical labels), asserted in
``tests/tools/test_clustering_tool.py``; this file only gates metric drift.

Run:  cd bloommcp && uv run --frozen python scripts/gen_clustering_golden.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import sleap_roots_analyze as sra
from sleap_roots_analyze import (
    GMMResult,
    KMeansResult,
    perform_gmm_clustering,
    perform_kmeans_clustering,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
_FINAL = _FIXTURES / "turface_19_final_data.csv"
_PCA_GOLDEN = _FIXTURES / "turface_19_pca_golden.json"
_OUT = _FIXTURES / "turface_19_clustering_golden.json"

_SEED = 42


def _metrics(result) -> dict:
    return {
        "n_clusters": int(result.n_clusters),
        "silhouette_score": float(result.silhouette_score),
        "davies_bouldin_score": float(result.davies_bouldin_score),
        "calinski_harabasz_score": float(result.calinski_harabasz_score),
        "cluster_sizes": [int(s) for s in result.cluster_sizes],
    }


def build() -> dict:
    trait_cols = json.loads(_PCA_GOLDEN.read_text())["trait_cols"]  # the recorded 8
    x = pd.read_csv(_FINAL)[trait_cols]

    kmeans = KMeansResult.from_kmeans_dict(
        perform_kmeans_clustering(
            x, n_clusters=3, standardize=True, random_state=_SEED
        ),
        random_state=_SEED,
    )
    gmm = GMMResult.from_gmm_dict(
        perform_gmm_clustering(
            x,
            n_components=3,
            covariance_type="full",
            standardize=True,
            random_state=_SEED,
        ),
        random_state=_SEED,
    )

    return {
        "_comment": (
            "Characterization snapshot of sleap-roots-analyze clustering on the post-QC "
            "turface_19 fixture, restricted to the 8 recorded PCA golden trait_cols. Used "
            "as a DRIFT GATE through the clustering MCP tool."
        ),
        "_source": (
            "Re-derived by bloommcp/scripts/gen_clustering_golden.py from "
            f"perform_kmeans_clustering / perform_gmm_clustering=={sra.__version__}. A drift "
            "gate, NOT an independently recorded oracle: no external clustering oracle exists "
            "for turface_19 (unlike PCA's #120 viz_pca_metadata.json). The real correctness "
            "oracle is determinism (same seed -> identical labels), asserted in the tests."
        ),
        "_reproduced_by_sleap_roots_analyze_version": sra.__version__,
        "trait_cols": trait_cols,
        "kmeans": {
            "params": {"n_clusters": 3, "standardize": True, "seed": _SEED},
            **_metrics(kmeans),
            "inertia": float(kmeans.inertia),
        },
        "gmm": {
            "params": {
                "n_components": 3,
                "covariance_type": "full",
                "standardize": True,
                "seed": _SEED,
            },
            **_metrics(gmm),
            "converged": bool(gmm.converged),
            "bic": float(gmm.bic),
            "aic": float(gmm.aic),
        },
    }


def main() -> None:
    payload = build()
    _OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {_OUT.relative_to(_FIXTURES.parents[1])}")


if __name__ == "__main__":
    main()
