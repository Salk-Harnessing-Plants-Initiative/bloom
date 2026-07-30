"""Live smoke: ``pca_analysis`` through the real running dev stack (#483).

Real MCP-transport call against both oracle fixtures. ``pca_analysis`` requires a
cleaned version (``require_clean=True``), so this calls ``qc_clean`` first. Cylinder's
846-trait case is well-conditioned for SVD-based PCA even at this scale (see
design.md), so it stays in the CI-safe (non-``live_smoke_slow``) subset.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.live_smoke


def test_pca_analysis_smoke(call_tool, seeded_experiment: str) -> None:
    call_tool("sleap_roots_qc_clean", {"experiment": seeded_experiment})

    result = call_tool(
        "sleap_roots_pca_analysis",
        {"experiment": seeded_experiment, "explained_variance_threshold": 0.75},
    )

    assert result["experiment"] == seeded_experiment
    assert result["n_samples"] > 0
    assert result["n_components"] > 0
    assert len(result["explained_variance_ratio"]) == result["n_components"]
    assert result["run_ref"]
    assert result["manifest_path"]
    # Regression pin for the RunLinks.outputs live-transport bug found via #489's
    # cross-experiment-correlations smoke test (fixed at the shared conftest.py level,
    # in _call_tool_sync -- see its docstring). That fix benefits every RunLinks-based
    # tool, but nothing beyond #489's own smoke test asserted on `outputs` to pin it
    # going forward (found in review) -- this is that second, independent tool.
    assert set(result["outputs"]) == {"loadings.csv", "scores.csv", "pca_result.json"}
