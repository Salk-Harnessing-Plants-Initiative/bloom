"""Live smoke: ``plot_trait_histograms`` through the real running dev stack (#483).

Cylinder is additionally marked ``live_smoke_slow``. ``plot_trait_histograms`` now
routes cylinder's 846 traits through ``create_trait_histograms_batched`` (53 pages)
instead of the unbatched single-figure delegate (see ``_viz_shared.py``'s
``TRAIT_BATCH_THRESHOLD``) -- fixing the same class of bug ``plot_heritability_bar``
hit, and producing legible paginated output for a real user. That pagination fix does
NOT meaningfully reduce total wall-clock time (still ~46-86s observed across runs,
same total rendering work just reorganized), so the cylinder case stays
``live_smoke_slow``: still too close to (and, per CI, sometimes past) the 120s client
timeout in ``conftest.py`` to run safely on every PR.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.live_smoke


@pytest.mark.parametrize(
    "fixture_name",
    ["turface_19", pytest.param("cylinder", marks=pytest.mark.live_smoke_slow)],
)
def test_plot_trait_histograms_smoke(
    call_plot_tool, assert_plot_success, seeded_experiment: str
) -> None:
    text = call_plot_tool(
        "sleap_roots_plot_trait_histograms", filename=seeded_experiment
    )
    assert_plot_success(text)
