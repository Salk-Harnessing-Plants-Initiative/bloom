"""Live smoke: ``heritability_analysis`` through the real running dev stack (#462).

Real MCP-transport call against both oracle fixtures. ``heritability_analysis`` requires a
cleaned version (``require_clean=True``), so this calls ``qc_clean`` first.

**Deliberately `live_smoke` only, not `live_smoke_slow`** — unlike the two tools it
replaced. ``plot_heritability_bar``/``plot_variance_decomposition`` read whole trait CSVs
out of ``TRAITS_DIR`` (846 traits at cylinder scale), which is what made a per-trait
MixedLM fit expensive enough to keep out of CI. This tool is a ``SupabaseReader``
consumer: it reads the DB-seeded smoke experiments, whose largest shape is an order of
magnitude smaller. Marking it slow would exclude it from ``python-audit`` (no stack up)
*and* from ``dev-stack-smoke``, leaving a tool-surface-breaking change with no per-PR
live-stack signal at all — strictly less coverage than its predecessors had.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.live_smoke


def test_heritability_analysis_smoke(call_tool, db_experiment_id: str) -> None:
    call_tool("sleap_roots_qc_clean", {"experiment": db_experiment_id})

    result = call_tool(
        "sleap_roots_heritability_analysis",
        {"experiment": db_experiment_id, "include_plots": True},
    )

    assert result["experiment"] == db_experiment_id
    assert result["n_samples"] > 0
    assert result["n_traits_reported"] > 0
    assert result["run_ref"]
    assert result["manifest_path"]
    assert result["method"]
    assert result["genotype_col"]

    # The counts must reconcile over the real reader's certified trait set, not only
    # over a FakeReader's.
    assert (
        result["n_traits_requested"] == result["n_traits_reported"] + result["n_failed"]
    )
    assert len(result["per_trait"]) == min(result["n_traits_reported"], 50)

    # Structural only — the seeded smoke experiments are synthetic and thin (a handful of
    # genotypes), so a given trait failing to fit is a legitimate outcome here. The exact
    # numbers are the unit golden's job, not this leg's.
    for row in result["per_trait"]:
        assert 0.0 <= row["h2"] <= 1.0
        assert row["passed_threshold"] == (row["h2"] >= result["threshold"])
        # A scored trait carrying both variance components as exactly 0 must be named —
        # its h2 is not a measurement whichever branch produced it.
        if row["var_genetic"] == 0.0 and row["var_residual"] == 0.0:
            assert row["trait"] in result["zero_variance_traits"]

    # Both retired tools' figures, from the one call that returned the numbers above.
    pngs = {k for k in result["outputs"] if k.endswith(".png")}
    assert "create_variance_decomposition_plot.png" in pngs or result["n_failed"] == (
        result["n_traits_requested"]
    )
    # create_heritability_plot paginates above 50 traits, so it lands either as one PNG
    # or as `_page<N>` entries — assert whichever shape this fixture produces, and that
    # exactly one of the two shapes appears.
    bar = {k for k in pngs if k.startswith("create_heritability_plot")}
    assert bar
    if result["n_traits_reported"] > 50:
        assert bar == {
            f"create_heritability_plot_page{i}.png" for i in range(1, len(bar) + 1)
        }
    else:
        assert bar == {"create_heritability_plot.png"}

    assert set(result["output_links"]) == set(result["outputs"])
    assert {"heritability.csv", "heritability_result.json"} <= set(result["outputs"])
