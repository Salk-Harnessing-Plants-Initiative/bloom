"""Live smoke: ``plot_trait_histograms`` through the real running dev stack (#483, #466).

Converged onto ``@as_mcp_tool`` + the ``ExperimentReader`` port (#466): like the 7 granular
analysis tools, it now routes through ``SupabaseReader``'s DB-only raw tier, so this test uses
the ``db_experiment_id``/``call_tool`` harness (not ``seeded_experiment``/``call_plot_tool``,
which it used pre-#466 — see ``conftest.py``). The fast/unmarked contract + batching-boundary
tests live in ``tests/tools/test_plot_trait_histograms_tool.py``; this test only proves the tool
round-trips correctly through the real container.

Cylinder is additionally marked ``live_smoke_slow``. ``plot_trait_histograms`` routes
cylinder's 846 traits through ``create_trait_histograms_batched`` (53 pages) instead of the
unbatched single-figure delegate (see ``_viz_shared.py``'s ``TRAIT_BATCH_THRESHOLD``) — the
batching itself is unchanged by #466 (only persistence is), and does NOT meaningfully reduce
total wall-clock time (still ~46-86s observed pre-#466, same total rendering work just
reorganized), so cylinder stays ``live_smoke_slow``.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.live_smoke


@pytest.mark.parametrize(
    "fixture_name",
    ["turface_19", pytest.param("cylinder", marks=pytest.mark.live_smoke_slow)],
)
def test_plot_trait_histograms_smoke(call_tool, db_experiment_id: str) -> None:
    result = call_tool(
        "sleap_roots_plot_trait_histograms", {"experiment": db_experiment_id}
    )

    assert result["experiment"] == db_experiment_id
    assert result["n_traits_plotted"] > 0
    assert result["outputs"]
    assert result["run_ref"]
    assert result["manifest_path"]
