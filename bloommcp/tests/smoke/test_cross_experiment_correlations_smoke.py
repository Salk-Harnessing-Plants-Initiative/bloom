"""Live smoke: ``cross_experiment_correlations`` through the real running dev stack (#489).

Real MCP-transport call seeding BOTH oracle fixtures (turface_19 + cylinder)
simultaneously -- unlike every other smoke test in this package, this tool needs two
experiments at once, so it does not use the parametrized ``seeded_experiment`` fixture
(which seeds exactly one per test). ``cross_experiment_correlations`` requires a cleaned
version on both sides, so this calls ``qc_clean`` on each first, proving the
qc_clean(x2) -> cross_experiment_correlations(require_clean=True) composition resolves
against the real Supabase-backed ports -- including the composite ``experiment``/
``based_on_version`` encoding (design.md D1) round-tripping through a real manifest.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.live_smoke

# Mirrors conftest.py's own constants (not imported across modules -- pytest does not
# treat this directory as a package, so a relative `from .conftest import ...` fails
# collection; every other file here relies on injected fixtures instead, but this tool
# needs TWO experiments seeded at once, which the single-experiment `seeded_experiment`
# fixture can't do).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_FIXTURES_DIR = _REPO_ROOT / "bloommcp" / "tests" / "fixtures"
_TRAITS_DIR = _REPO_ROOT / "bloommcp" / "data" / "TRAITS_DIR"
_FIXTURE_FILES = {
    "turface_19": "turface_19_raw_data.csv",
    "cylinder": "cylinder_raw_data.csv",
}


def test_cross_experiment_correlations_smoke(call_tool) -> None:
    _TRAITS_DIR.mkdir(parents=True, exist_ok=True)
    turface_file = _FIXTURE_FILES["turface_19"]
    cylinder_file = _FIXTURE_FILES["cylinder"]
    shutil.copy(_FIXTURES_DIR / turface_file, _TRAITS_DIR / turface_file)
    shutil.copy(_FIXTURES_DIR / cylinder_file, _TRAITS_DIR / cylinder_file)

    call_tool("sleap_roots_qc_clean", {"experiment": turface_file})
    call_tool("sleap_roots_qc_clean", {"experiment": cylinder_file})

    result = call_tool(
        "sleap_roots_cross_experiment_correlations",
        {
            "experiment_1": turface_file,
            "experiment_2": cylinder_file,
            "min_samples": 3,
        },
    )

    assert result["experiment_1"] == turface_file
    assert result["experiment_2"] == cylinder_file
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
