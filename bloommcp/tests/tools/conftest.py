"""Shared fixtures for `tests/tools/` (#713).

`viz_env` moved here from `test_viz_tools.py` so `test_viz_snapshot.py` can reuse the
exact same real-TRAITS_DIR-read / real-PLOTS_DIR-write / manifest-miss setup rather than
maintaining a second copy that could silently desync (same rationale `_viz_shared.py`
gives for single-sourcing `save_plot`/`save_plot_or_plots` across the 5 plot tools).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from bloom_mcp import experiment_utils as eu
from bloom_mcp.sections.sleap_roots.analysis import _viz_shared

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_RAW = _FIXTURES / "turface_19_final_data.csv"
_EXPERIMENT = "turface_19.csv"


@pytest.fixture
def viz_env(monkeypatch, tmp_path, fake_supabase_storage):
    """Real TRAITS_DIR read + PLOTS_DIR write, versioned-manifest lookup misses."""
    traits = tmp_path / "traits"
    traits.mkdir()
    shutil.copy(_RAW, traits / _EXPERIMENT)
    monkeypatch.setattr(eu, "TRAITS_DIR", traits)

    plots = tmp_path / "plots"
    monkeypatch.setattr(eu, "PLOTS_DIR", plots)
    monkeypatch.setattr(_viz_shared, "PLOTS_DIR", plots)
    return plots
