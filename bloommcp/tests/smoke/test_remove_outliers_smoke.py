"""Live smoke: ``remove_outliers`` (isolation_forest) through the real running dev
stack (#483).

Real MCP-transport call against both oracle fixtures. Cylinder's case is additionally
marked ``live_smoke_slow``: 846 traits vs 129 samples makes the trait-covariance matrix
severely rank-deficient (see ``cylinder_outlier_golden.json``'s ``"poor"``
``goodness_of_fit_fit_quality``) -- the same numerically-risky class the ``integration``
marker already exists to contain for the oracle tests. The fast/unmarked oracle
assertions live in ``tests/tools/test_remove_outliers_tool.py``.

**method=isolation_forest, not mahalanobis (#419):** both oracle fixtures have an
untrustworthy mahalanobis fit at their canonical cleaning threshold (turface_19
very_poor, cylinder poor — see ``test_remove_outliers_tool.py``'s golden fixtures), so
a mahalanobis call here would hit the fit-trustworthiness gate and raise
``assumption_violated`` instead of persisting — this smoke exists to prove real
MCP-transport persistence, not to re-characterize the fit. isolation_forest is never
gated.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.live_smoke


@pytest.mark.parametrize(
    "fixture_name",
    ["turface_19", pytest.param("cylinder", marks=pytest.mark.live_smoke_slow)],
)
def test_remove_outliers_smoke(call_tool, db_experiment_id: str) -> None:
    call_tool("sleap_roots_qc_clean", {"experiment": db_experiment_id})

    result = call_tool(
        "sleap_roots_remove_outliers",
        {"experiment": db_experiment_id, "method": "isolation_forest", "seed": 42},
    )

    assert result["experiment"] == db_experiment_id
    assert result["method"] == "isolation_forest"
    assert result["n_input_samples"] > 0
    assert result["n_output_samples"] <= result["n_input_samples"]
    assert (
        result["n_outliers"] == result["n_input_samples"] - result["n_output_samples"]
    )
    assert result["run_ref"]
    assert result["manifest_path"]
