"""Live smoke: ``plot_trait_histograms`` through the real running dev stack (#483).

Real MCP-transport call (matching ``live_plot_tool_smoke.py``'s pattern) against both
oracle fixtures -- proves the tool writes to the real bind-mounted PLOTS_DIR through
the container, not just via an in-process call. Works directly on the raw fixture
(no qc_clean prerequisite -- plot tools are plain functions, not require_clean).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.live_smoke


def test_plot_trait_histograms_smoke(call_plot_tool, seeded_experiment: str) -> None:
    text = call_plot_tool(
        "sleap_roots_plot_trait_histograms", filename=seeded_experiment
    )
    assert "Plot saved:" in text
    assert "denied" not in text.lower()
