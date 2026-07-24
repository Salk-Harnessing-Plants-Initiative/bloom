"""Live smoke: ``remove_outliers`` (mahalanobis) through the real running dev stack
(#483).

Real MCP-transport call against both oracle fixtures. Cylinder's case is additionally
marked ``live_smoke_slow``: 846 traits vs 129 samples makes the trait-covariance matrix
severely rank-deficient (see ``cylinder_outlier_golden.json``'s ``"poor"``
``goodness_of_fit_fit_quality``) -- the same numerically-risky class the ``integration``
marker already exists to contain for the oracle tests. The fast/unmarked oracle
assertions live in ``tests/tools/test_remove_outliers_tool.py``.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.live_smoke


@pytest.mark.parametrize(
    "fixture_name",
    ["turface_19", pytest.param("cylinder", marks=pytest.mark.live_smoke_slow)],
)
def test_remove_outliers_smoke(call_tool, seeded_experiment: str) -> None:
    call_tool("sleap_roots_qc_clean", {"experiment": seeded_experiment})

    result = call_tool(
        "sleap_roots_remove_outliers",
        {"experiment": seeded_experiment, "method": "mahalanobis", "seed": 42},
    )

    assert result["experiment"] == seeded_experiment
    assert result["method"] == "mahalanobis"
    assert result["n_input_samples"] > 0
    assert result["n_output_samples"] <= result["n_input_samples"]
    assert result["n_outliers"] == result["n_input_samples"] - result["n_output_samples"]
    assert result["run_ref"]
    assert result["manifest_path"]
