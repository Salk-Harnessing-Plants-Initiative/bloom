"""Live smoke: ``plot_trait_boxplots`` through the real running dev stack (#483, #466).

Converged onto ``@as_mcp_tool`` + the ``ExperimentReader`` port (#466): like the 7 granular
analysis tools, it now routes through ``SupabaseReader``'s DB-only raw tier, so this test uses
the ``db_experiment_id``/``call_tool`` harness (not ``seeded_experiment``/``call_plot_tool``,
which it used pre-#466 — see ``conftest.py``). The fast/unmarked contract + batching-boundary
tests live in ``tests/tools/test_plot_trait_boxplots_tool.py``; this test only proves the tool
round-trips correctly through the real container.

Cylinder is additionally marked ``live_smoke_slow``. ``plot_trait_boxplots`` routes cylinder's
846 traits through ``create_trait_boxplots_by_genotype_batched`` (53 pages) instead of the
unbatched single-figure delegate (see ``_viz_shared.py``'s ``TRAIT_BATCH_THRESHOLD``) — the
batching itself is unchanged by #466 (only persistence is), and does NOT meaningfully reduce
total wall-clock time (still ~109-111s observed pre-#466), so cylinder stays
``live_smoke_slow``: it exceeded the 120s client timeout in CI (the original failure that
prompted the pagination fix + this marker).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.live_smoke


@pytest.mark.parametrize(
    "fixture_name",
    ["turface_19", pytest.param("cylinder", marks=pytest.mark.live_smoke_slow)],
)
def test_plot_trait_boxplots_smoke(call_tool, db_experiment_id: str) -> None:
    result = call_tool(
        "sleap_roots_plot_trait_boxplots", {"experiment": db_experiment_id}
    )

    assert result["experiment"] == db_experiment_id
    assert result["genotype_column"]
    assert result["n_traits_plotted"] > 0
    assert result["outputs"]
    assert result["run_ref"]
    assert result["manifest_path"]

    # #748 sample-size disclosure, through the real server. Cylinder is exactly the scale the
    # caps exist for: ~846 traits x ~19 genotypes = ~16,000 cells, of which ~5% sit below the
    # floor -- so this asserts the shape and internal consistency of the disclosure rather
    # than specific counts, which are data-dependent.
    assert "group_sample_sizes.csv" in result["outputs"]
    # The table is an output, not a page.
    assert "group_sample_sizes.csv" not in result["page_traits"]
    assert len(result["outputs"]) == result["n_pages"] + 1
    assert result["n_genotype_groups"] > 0
    assert result["n_boxes_summarized"] <= result["n_boxes_drawn"]
    assert (
        result["n_boxes_drawn"]
        + result["absent_genotype_group_count"]
        + result["no_data_trait_count"] * result["n_genotype_groups"]
        == result["n_traits_plotted"] * result["n_genotype_groups"]
    )
    assert result["box_n_min"] <= result["box_n_median"] <= result["box_n_max"]
    assert len(result["small_sample_groups"]) <= result["small_sample_group_count"]
    # The note is the only signal a caller who opens just the PNG gets.
    assert "n per box" in result["sample_size_note"]
