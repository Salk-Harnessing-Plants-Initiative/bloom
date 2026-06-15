"""Cross-tier oracle: the delegated sleap-roots-analyze analysis reproduces the
independently recorded #120 turface_19 golden values.

The input table and golden values are vendored from the sleap-roots-analyze
#120 / PR #146 fixtures (see ``tests/fixtures/README.md``); they are NOT
re-derived from the library under test, so a numeric drift in
``perform_pca_analysis`` would fail this test. Assertions are explicit with a
stated tolerance — no auto-generated snapshot.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from sleap_roots_analyze.pca import perform_pca_analysis

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_TOL = 1e-6


@pytest.fixture(scope="module")
def turface_19():
    df = pd.read_csv(_FIXTURES / "turface_19_final_data.csv")
    golden = json.loads((_FIXTURES / "turface_19_pca_golden.json").read_text())
    return df, golden


def test_pca_reproduces_recorded_component_count(turface_19):
    df, golden = turface_19
    result = perform_pca_analysis(
        df[golden["trait_cols"]], explained_variance_threshold=0.95
    )
    assert result["n_components_selected"] == golden["n_pca_components"]


def test_pca_reproduces_recorded_explained_variance(turface_19):
    df, golden = turface_19
    result = perform_pca_analysis(
        df[golden["trait_cols"]], explained_variance_threshold=0.95
    )
    n = golden["n_pca_components"]
    cumulative_at_cut = float(result["cumulative_variance_ratio"][n - 1])
    assert cumulative_at_cut == pytest.approx(
        golden["pca_explained_variance"], abs=_TOL
    )
