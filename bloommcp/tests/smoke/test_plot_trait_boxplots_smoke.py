"""Live smoke: ``plot_trait_boxplots`` through the real running dev stack (#483).

Cylinder is additionally marked ``live_smoke_slow``. Unlike ``plot_heritability_bar``,
this isn't a code bug -- ``create_trait_boxplots_by_genotype`` has no pagination and
renders all 846 traits (grouped by genotype) into a single figure, which is genuinely
slow and, per CI's own first run, variable enough to sit right at (and sometimes past)
the 120s client timeout in ``conftest.py`` (observed 46-109s locally across runs, CI
saw a >120s timeout) -- not "bounded time" the way design.md originally assumed for
matplotlib-only rendering.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.live_smoke


@pytest.mark.parametrize(
    "fixture_name",
    ["turface_19", pytest.param("cylinder", marks=pytest.mark.live_smoke_slow)],
)
def test_plot_trait_boxplots_smoke(call_plot_tool, seeded_experiment: str) -> None:
    text = call_plot_tool("sleap_roots_plot_trait_boxplots", filename=seeded_experiment)
    assert "Plot saved:" in text
    assert "denied" not in text.lower()
