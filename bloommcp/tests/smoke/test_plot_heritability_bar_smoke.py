"""Live smoke: ``plot_heritability_bar`` through the real running dev stack (#483).

Marked ``live_smoke_slow`` on BOTH fixtures: delegates to
``calculate_heritability_estimates``, which fits a ``statsmodels.MixedLM`` **per
trait** -- the same computation family already flagged CI-flaky for
``test_oracle.py``'s ``integration``-marked oracle tests, but now potentially
hundreds of sequential fits (846 traits for cylinder) instead of ~18.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.live_smoke, pytest.mark.live_smoke_slow]


def test_plot_heritability_bar_smoke(
    call_plot_tool, assert_plot_success, seeded_experiment: str
) -> None:
    text = call_plot_tool("sleap_roots_plot_heritability_bar", filename=seeded_experiment)
    assert_plot_success(text)
