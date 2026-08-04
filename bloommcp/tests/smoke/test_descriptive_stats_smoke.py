"""Live smoke: ``descriptive_stats`` through the real running dev stack (#488).

Real MCP-transport call against both oracle fixtures. ``descriptive_stats`` requires a
cleaned version (``require_clean=True``), so this calls ``qc_clean`` first. Cylinder's
wide trait table (~649-880 traits post-QC) exercises the 50-trait inline cap for real,
not only via the synthetic unit test.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.live_smoke


def test_descriptive_stats_smoke(
    call_tool, db_experiment_id: str, fixture_name: str
) -> None:
    call_tool("sleap_roots_qc_clean", {"experiment": db_experiment_id})

    result = call_tool(
        "sleap_roots_descriptive_stats",
        {"experiment": db_experiment_id},
    )

    assert result["experiment"] == db_experiment_id
    assert result["n_traits_reported"] > 0
    assert result["n_failed"] == 0
    assert result["run_ref"]
    assert result["manifest_path"]

    if fixture_name == "cylinder":
        # Cylinder's post-QC trait count comfortably exceeds the 50-trait cap --
        # exercise truncated_in_summary/omitted_traits for real.
        assert result["truncated_in_summary"] is True
        assert len(result["stats_per_trait"]) == 50
        assert len(result["omitted_traits"]) == result["n_traits_reported"] - 50
