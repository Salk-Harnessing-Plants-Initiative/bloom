"""Live smoke: ``plot_correlation_matrix`` through the real running dev stack (#483, #466).

Converged onto ``@as_mcp_tool`` + the ``ExperimentReader`` port (#466): like the 7 granular
analysis tools, it now routes through ``SupabaseReader``'s DB-only raw tier, so this test uses
the ``db_experiment_id``/``call_tool`` harness (not ``seeded_experiment``/``call_plot_tool``,
which it used pre-#466 — see ``conftest.py``). The fast/unmarked contract + numeric-oracle
tests live in ``tests/tools/test_plot_correlation_matrix_tool.py``; this test only proves the
tool round-trips correctly through the real container.

Cylinder is additionally marked ``live_smoke_slow``: an ~846x846 correlation matrix + heatmap
render is meaningfully more wall-clock work than turface_19's 20-trait version (not numerically
unstable, just slower -- see design.md).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.live_smoke


@pytest.mark.parametrize(
    "fixture_name",
    ["turface_19", pytest.param("cylinder", marks=pytest.mark.live_smoke_slow)],
)
def test_plot_correlation_matrix_smoke(call_tool, db_experiment_id: str) -> None:
    result = call_tool(
        "sleap_roots_plot_correlation_matrix", {"experiment": db_experiment_id}
    )

    assert result["experiment"] == db_experiment_id
    assert result["n_traits"] > 0
    assert result["outputs"]
    assert result["run_ref"]
    assert result["manifest_path"]
