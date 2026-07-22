"""Live smoke: ``plot_correlation_matrix`` through the real running dev stack (#483).

Cylinder is additionally marked ``live_smoke_slow``: an ~846x846 correlation matrix +
heatmap render is meaningfully more wall-clock work than turface_19's 20-trait version
(not numerically unstable, just slower -- see design.md).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.live_smoke


@pytest.mark.parametrize(
    "fixture_name",
    ["turface_19", pytest.param("cylinder", marks=pytest.mark.live_smoke_slow)],
)
def test_plot_correlation_matrix_smoke(call_plot_tool, seeded_experiment: str) -> None:
    text = call_plot_tool(
        "sleap_roots_plot_correlation_matrix", filename=seeded_experiment
    )
    assert "Plot saved:" in text
    assert "denied" not in text.lower()
