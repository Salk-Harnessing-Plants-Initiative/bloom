"""Live smoke: ``cross_experiment_correlations`` through the real running dev stack (#489).

Real MCP-transport call resolving BOTH oracle fixtures (turface_19 + cylinder)
simultaneously -- unlike every other smoke test in this package, this tool needs two
experiments at once, so it does not use the parametrized ``db_experiment_id`` fixture
(which resolves exactly one per test). ``cross_experiment_correlations`` requires a cleaned
version on both sides, so this calls ``qc_clean`` on each first, proving the
qc_clean(x2) -> cross_experiment_correlations(require_clean=True) composition resolves
against the real Supabase-backed ports -- including the composite ``experiment``/
``based_on_version`` encoding (design.md D1) round-tripping through a real manifest.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.live_smoke

# Mirrors conftest.py's own EXPERIMENT_ID_ENV_VARS (not imported across modules -- pytest
# does not treat this directory as a package, so a relative `from .conftest import ...`
# fails collection; every other file here relies on injected fixtures instead, but this
# tool needs TWO experiments resolved at once, which the single-experiment
# `db_experiment_id` fixture can't do).
#
# SupabaseReader's raw tier is DB-only (bloom#551): a tool call needs a numeric
# experiment id, not a filename -- there is no local-CSV upload path left for this (or
# any of the other 6 granular analysis) tool to seed itself from.
_EXPERIMENT_ID_ENV_VARS = {
    "turface_19": "BLOOM_SMOKE_EXPERIMENT_ID_TURFACE_19",
    "cylinder": "BLOOM_SMOKE_EXPERIMENT_ID_CYLINDER",
}


def _db_experiment_id(fixture_name: str) -> str:
    env_var = _EXPERIMENT_ID_ENV_VARS[fixture_name]
    experiment_id = os.environ.get(env_var, "")
    if not experiment_id:
        pytest.skip(
            f"{env_var} is unset -- set it to a numeric experiment id already seeded "
            f"with trait rows in Postgres for the {fixture_name!r} oracle fixture "
            "(SupabaseReader's raw tier is DB-only; there is no local-CSV upload path "
            "to fall back to)."
        )
    return experiment_id


def test_cross_experiment_correlations_smoke(call_tool) -> None:
    turface_id = _db_experiment_id("turface_19")
    cylinder_id = _db_experiment_id("cylinder")

    call_tool("sleap_roots_qc_clean", {"experiment": turface_id})
    call_tool("sleap_roots_qc_clean", {"experiment": cylinder_id})

    result = call_tool(
        "sleap_roots_cross_experiment_correlations",
        {
            "experiment_1": turface_id,
            "experiment_2": cylinder_id,
            "min_samples": 3,
        },
    )

    assert result["experiment_1"] == turface_id
    assert result["experiment_2"] == cylinder_id
    # Both sides consumed a committed cleaned version, not raw (require_clean=True).
    assert result["source_1"].endswith("_cleaned")
    assert result["source_2"].endswith("_cleaned")
    assert result["n_correlations"] > 0
    assert result["run_ref"]
    assert result["manifest_path"]
    assert set(result["outputs"]) == {
        "correlations.csv",
        "significant.csv",
        "genotype_means_1.csv",
        "genotype_means_2.csv",
        "summary.json",
    }
