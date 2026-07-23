"""Live smoke: ``plot_variance_decomposition`` through the real running dev stack
(#483).

Marked ``live_smoke_slow`` on BOTH fixtures: shares the same per-trait
``statsmodels.MixedLM`` delegate as ``plot_heritability_bar`` (see
``plot_variance_decomposition.py`` vs ``plot_heritability_bar.py`` -- identical
``calculate_heritability_estimates`` call), so it carries the identical CI-flakiness
risk. Issue #483 never analyzes this 5th plotting tool; it gets the same
classification here because it shares its delegate line-for-line.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.live_smoke, pytest.mark.live_smoke_slow]


def test_plot_variance_decomposition_smoke(
    call_plot_tool, assert_plot_success, seeded_experiment: str
) -> None:
    text = call_plot_tool(
        "sleap_roots_plot_variance_decomposition", filename=seeded_experiment
    )
    assert_plot_success(text)
