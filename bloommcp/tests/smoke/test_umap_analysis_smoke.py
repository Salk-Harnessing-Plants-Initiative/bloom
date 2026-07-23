"""Live smoke: ``umap_analysis`` through the real running dev stack (#425).

Real MCP-transport call against the turface_19 oracle fixture. ``umap_analysis``
requires a cleaned version (``require_clean=True``), so this calls ``qc_clean`` first.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.live_smoke


def test_umap_analysis_smoke(call_tool, seeded_experiment: str) -> None:
    call_tool("sleap_roots_qc_clean", {"experiment": seeded_experiment})

    result = call_tool(
        "sleap_roots_umap_analysis",
        {"experiment": seeded_experiment, "seed": 42},
    )

    assert result["experiment"] == seeded_experiment
    assert result["n_samples"] > 0
    assert result["n_components"] == 2
    assert result["seed"] == 42
    assert result["run_ref"]
    assert result["manifest_path"]
