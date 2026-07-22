"""Live smoke: ``plot_trait_boxplots`` through the real running dev stack (#483)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.live_smoke


def test_plot_trait_boxplots_smoke(call_plot_tool, seeded_experiment: str) -> None:
    text = call_plot_tool("sleap_roots_plot_trait_boxplots", filename=seeded_experiment)
    assert "Plot saved:" in text
    assert "denied" not in text.lower()
