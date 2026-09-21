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

import math

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
    assert result["n_traits_plotted"] > 0
    assert result["outputs"]
    assert result["run_ref"]
    assert result["manifest_path"]

    # #466 review round 4: cylinder's raw ~846-trait data is exactly the disjoint-per-trait-
    # missingness case zero_variance_traits/low_overlap_trait_pairs/heatmap_caveat target,
    # and this was previously the only place these 3 fields went entirely unasserted.
    assert result["resolved_trait_columns"]
    assert isinstance(result["zero_variance_traits"], list)
    assert isinstance(result["low_overlap_trait_pairs"], list)
    if result["zero_variance_traits"] or result["low_overlap_trait_pairs"]:
        assert result["heatmap_caveat"] is not None
    else:
        assert result["heatmap_caveat"] is None

    # #784/#785: cylinder's ~846 traits are the scale the two caps exist for.
    #
    # Scope, stated honestly (#784 review): the cylinder parametrization carries
    # `live_smoke_slow`, and CI runs `-m "live_smoke and not live_smoke_slow"`, so only
    # turface_19 (20 traits, below both caps) gates a PR. This test is a real round-trip
    # through the container, NOT the gate on the capped-list/uncapped-scalar contract —
    # that is pinned by the unit tests in tests/tools/, which do run in CI.
    #
    # The assertions below are also written to be non-vacuous: every one of them used to
    # hold trivially on an empty response, so a regression that emptied both new fields
    # would have stayed green.
    assert isinstance(result["locally_constant_trait_pairs"], list)
    assert result["locally_constant_pair_count"] >= len(
        result["locally_constant_trait_pairs"]
    )
    pairs = result["strong_correlation_pairs"]
    assert isinstance(pairs, list)
    assert [p["overlap_n"] for p in pairs] == sorted(p["overlap_n"] for p in pairs)
    for pair in pairs:
        # A null CI is legitimate (|r| == 1); a non-finite one is not — it would have made
        # the manifest invalid JSON.
        for bound in (pair["ci_low"], pair["ci_high"]):
            assert bound is None or math.isfinite(bound)
    # Non-vacuity: a real experiment's traits are correlated, so an empty list here means
    # the field regressed, not that the data is clean.
    assert pairs, "expected at least one strong pair on real experiment data"
    assert result["strong_pair_count"] == len(pairs) or result[
        "strong_pair_count"
    ] > len(pairs), "strong_pair_count must be the uncapped total"
    assert result["strong_pair_count"] == (
        result["strong_positive_correlations"] + result["strong_negative_correlations"]
    )
    assert result["strong_pair_overlap_min"] == pairs[0]["overlap_n"]
    assert result["strong_pair_overlap_min"] <= result["strong_pair_overlap_max"]
    assert (
        result["strong_pair_overlap_min"]
        <= result["strong_pair_overlap_median"]
        <= result["strong_pair_overlap_max"]
    )
