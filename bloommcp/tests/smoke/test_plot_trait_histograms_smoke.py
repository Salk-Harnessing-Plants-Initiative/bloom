"""Live smoke: ``plot_trait_histograms`` through the real running dev stack (#483).

Cylinder is additionally marked ``live_smoke_slow``: ``create_trait_histograms`` has
no pagination and renders all 846 traits into a single figure. Observed 46-86s across
runs locally -- close enough to (and, combined with ``plot_trait_boxplots``'s >120s CI
timeout, evidently variable enough past) the 120s client timeout in ``conftest.py``
that this isn't reliably "bounded time" at cylinder's scale, despite design.md's
original assumption that matplotlib-only rendering over already-computed values was
inherently cheap.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.live_smoke


@pytest.mark.parametrize(
    "fixture_name",
    ["turface_19", pytest.param("cylinder", marks=pytest.mark.live_smoke_slow)],
)
def test_plot_trait_histograms_smoke(call_plot_tool, seeded_experiment: str) -> None:
    text = call_plot_tool(
        "sleap_roots_plot_trait_histograms", filename=seeded_experiment
    )
    assert "Plot saved:" in text
    assert "denied" not in text.lower()
