"""Regenerate ``tests/fixtures/turface_cylinder_cross_experiment_correlation_golden.json``
(#489).

A characterization snapshot pinning both the correlation math AND the confirmed upstream
``min_samples`` no-op this tool works around (design.md D8,
talmolab/sleap-roots-analyze#205): every literal is emitted here from
``sleap_roots_analyze.calculate_genotype_means`` /
``calculate_cross_experiment_correlations`` on the real turface_19 / cylinder fixtures
(19 shared genotypes), so the fixture is regenerable and no number is transcribed by hand.

Records BOTH the unfiltered call (min_samples=1, all 19 genotypes participate — the
correlation math oracle) and the min_samples=3 comparison: calling the upstream delegate
directly with min_samples=3 still returns all 19 genotypes (the confirmed no-op — cylinder's
GH_7371 has only 2 samples and should have been excluded), while bloommcp's pre-filter
workaround (drop genotype rows with n_samples < min_samples from both means tables before
delegating) correctly excludes it, yielding 18 genotypes and a different correlation value.
This is the drift gate for ``test_min_samples_prefilter_actually_excludes_under_replicated_genotypes``.

Run:  cd bloommcp && uv run --frozen python scripts/gen_cross_experiment_correlation_golden.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import sleap_roots_analyze as sra
from sleap_roots_analyze import (
    calculate_cross_experiment_correlations,
    calculate_genotype_means,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
_TURFACE = _FIXTURES / "turface_19_final_data.csv"
_CYLINDER = _FIXTURES / "cylinder_final_data.csv"
_OUT = _FIXTURES / "turface_cylinder_cross_experiment_correlation_golden.json"

_TRAIT_1 = "Total Root Length (mm)"  # turface_19
_TRAIT_2 = "Network Length Mean"  # cylinder
_GENOTYPE_COL = "Genotype"


def build() -> dict:
    turface = pd.read_csv(_TURFACE)
    cylinder = pd.read_csv(_CYLINDER)

    gm1 = calculate_genotype_means(turface, [_TRAIT_1], genotype_col=_GENOTYPE_COL)
    gm2 = calculate_genotype_means(cylinder, [_TRAIT_2], genotype_col=_GENOTYPE_COL)

    unfiltered = calculate_cross_experiment_correlations(
        gm1, gm2, [_TRAIT_1], [_TRAIT_2], min_samples=1
    )
    row_u = unfiltered.iloc[0]

    # Confirms the upstream no-op directly on this fixture: passing min_samples=3 to the
    # delegate WITHOUT a bloommcp-side pre-filter still uses all 19 genotypes (cylinder's
    # GH_7371, n_samples=2, is never excluded).
    buggy = calculate_cross_experiment_correlations(
        gm1, gm2, [_TRAIT_1], [_TRAIT_2], min_samples=3
    )
    row_buggy = buggy.iloc[0]

    # bloommcp's pre-filter workaround (design.md D8): drop genotype rows below
    # min_samples from BOTH means tables before delegating.
    gm1_f = gm1[gm1["n_samples"] >= 3]
    gm2_f = gm2[gm2["n_samples"] >= 3]
    prefiltered = calculate_cross_experiment_correlations(
        gm1_f, gm2_f, [_TRAIT_1], [_TRAIT_2], min_samples=3
    )
    row_f = prefiltered.iloc[0]

    return {
        "_comment": (
            "Characterization snapshot of sleap-roots-analyze cross-experiment "
            "correlation on the real turface_19 (153 samples, 19 genotypes) / cylinder "
            "(123 samples, 19 genotypes, fully overlapping) fixture pair. Used as a DRIFT "
            "GATE + regression fixture for the confirmed min_samples no-op workaround "
            "(design.md D8) through the cross_experiment_correlations MCP tool."
        ),
        "_source": (
            "Re-derived by bloommcp/scripts/gen_cross_experiment_correlation_golden.py "
            f"from calculate_genotype_means / calculate_cross_experiment_correlations=="
            f"{sra.__version__}. No number here is hand-transcribed."
        ),
        "_reproduced_by_sleap_roots_analyze_version": sra.__version__,
        "experiment_1": "turface_19.csv",
        "experiment_2": "cylinder.csv",
        "genotype_col": _GENOTYPE_COL,
        "trait_1": _TRAIT_1,
        "trait_2": _TRAIT_2,
        "unfiltered": {
            "_comment": "min_samples=1 -> all 19 shared genotypes participate.",
            "min_samples": 1,
            "n_genotypes": int(row_u["n_genotypes"]),
            "correlation": float(row_u["correlation"]),
            "p_value": float(row_u["p_value"]),
            "significant": bool(row_u["significant"]),
        },
        "min_samples_3_upstream_no_op": {
            "_comment": (
                "Calling the upstream delegate directly with min_samples=3 (no bloommcp "
                "pre-filter) — confirms the no-op: n_genotypes is unchanged from the "
                "unfiltered call (still 19), even though cylinder's GH_7371 has only 2 "
                "samples. See talmolab/sleap-roots-analyze#205."
            ),
            "min_samples": 3,
            "n_genotypes": int(row_buggy["n_genotypes"]),
            "correlation": float(row_buggy["correlation"]),
            "p_value": float(row_buggy["p_value"]),
        },
        "min_samples_3_bloommcp_prefiltered": {
            "_comment": (
                "bloommcp's pre-filter workaround (design.md D8) applied before "
                "delegating: genotype rows with n_samples < 3 dropped from both means "
                "tables first. GH_7371 (cylinder n_samples=2) is correctly excluded, "
                "n_genotypes drops to 18, and the correlation value changes."
            ),
            "min_samples": 3,
            "n_genotypes": int(row_f["n_genotypes"]),
            "correlation": float(row_f["correlation"]),
            "p_value": float(row_f["p_value"]),
            "excluded_genotype": "GH_7371",
        },
    }


def main() -> None:
    payload = build()
    _OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {_OUT.relative_to(_FIXTURES.parents[1])}")


if __name__ == "__main__":
    main()
