"""Shared fixtures for `tests/tools/` (#713).

`viz_env` moved here from the former `test_viz_tools.py` so `test_viz_snapshot.py` could
reuse the exact same real-TRAITS_DIR-read / manifest-miss setup rather than maintain a
second copy that could silently desync. Post-#462 no plot tool writes to `PLOTS_DIR` (the
last two that did were retired into `heritability_analysis`), so the fixture's `plots`
directory now serves only as the scratch location `test_viz_snapshot.py` copies committed
bytes into.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from bloom_mcp import experiment_utils as eu

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_RAW = _FIXTURES / "turface_19_final_data.csv"
_EXPERIMENT = "turface_19.csv"


@pytest.fixture
def viz_env(monkeypatch, tmp_path, fake_supabase_storage):
    """Real TRAITS_DIR read, versioned-manifest lookup misses; `plots` is scratch space."""
    traits = tmp_path / "traits"
    traits.mkdir()
    shutil.copy(_RAW, traits / _EXPERIMENT)
    monkeypatch.setattr(eu, "TRAITS_DIR", traits)

    plots = tmp_path / "plots"
    monkeypatch.setattr(eu, "PLOTS_DIR", plots)
    return plots
