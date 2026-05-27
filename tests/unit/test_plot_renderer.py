"""Unit tests for the shared plot-rendering helper.

Verifies the filename shape, permission setting, env-var failure modes, and
namespace separation. Uses tmp_path so no real BLOOM_PLOTS_DIR is touched.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "langchain"))

import matplotlib

matplotlib.use("Agg")
from matplotlib.figure import Figure  # noqa: E402

from helpers.plot_renderer import render_and_save  # noqa: E402


def _make_fig() -> Figure:
    fig = Figure(figsize=(4, 3))
    ax = fig.add_subplot(111)
    ax.plot([0, 1, 2], [0, 1, 4])
    return fig


def test_saves_png_with_correct_filename_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOOM_PLOTS_DIR", str(tmp_path))
    monkeypatch.setenv("BLOOM_PLOTS_URL", "http://localhost:5002/plots")

    url = render_and_save(_make_fig(), prefix="accession_rank", namespace="cyl_supabase")

    assert url.startswith("http://localhost:5002/plots/")
    assert re.search(r"cyl_supabase_accession_rank_[0-9a-f]{8}\.png$", url)

    # Exactly one PNG file written, matching the URL filename
    files = list(tmp_path.glob("*.png"))
    assert len(files) == 1
    assert files[0].name == url.rsplit("/", 1)[-1]


def test_namespace_separates_filenames(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOOM_PLOTS_DIR", str(tmp_path))
    monkeypatch.setenv("BLOOM_PLOTS_URL", "http://localhost:5002/plots")

    render_and_save(_make_fig(), prefix="hist", namespace="cyl_supabase")
    render_and_save(_make_fig(), prefix="hist", namespace="scrna_supabase")

    names = sorted(f.name for f in tmp_path.glob("*.png"))
    assert len(names) == 2
    assert names[0].startswith("cyl_supabase_hist_")
    assert names[1].startswith("scrna_supabase_hist_")


def test_file_mode_is_0644(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOOM_PLOTS_DIR", str(tmp_path))
    monkeypatch.setenv("BLOOM_PLOTS_URL", "http://localhost:5002/plots")

    render_and_save(_make_fig(), prefix="x", namespace="cyl_supabase")

    files = list(tmp_path.glob("*.png"))
    mode = files[0].stat().st_mode & 0o777
    assert mode == 0o644


def test_trailing_slash_in_plots_url_handled(tmp_path, monkeypatch):
    """URL construction shouldn't produce double slashes."""
    monkeypatch.setenv("BLOOM_PLOTS_DIR", str(tmp_path))
    monkeypatch.setenv("BLOOM_PLOTS_URL", "http://localhost:5002/plots/")

    url = render_and_save(_make_fig(), prefix="x", namespace="cyl_supabase")

    assert "//cyl_supabase_" not in url
    assert url.startswith("http://localhost:5002/plots/cyl_supabase_")


def test_concurrent_writes_get_distinct_uuids(tmp_path, monkeypatch):
    """Same prefix + namespace called twice in a row gets different filenames."""
    monkeypatch.setenv("BLOOM_PLOTS_DIR", str(tmp_path))
    monkeypatch.setenv("BLOOM_PLOTS_URL", "http://localhost:5002/plots")

    a = render_and_save(_make_fig(), prefix="dist", namespace="cyl_supabase")
    b = render_and_save(_make_fig(), prefix="dist", namespace="cyl_supabase")

    assert a != b
    assert len(list(tmp_path.glob("*.png"))) == 2


def test_missing_plots_dir_raises(monkeypatch):
    monkeypatch.delenv("BLOOM_PLOTS_DIR", raising=False)
    monkeypatch.setenv("BLOOM_PLOTS_URL", "http://localhost:5002/plots")

    with pytest.raises(RuntimeError, match="BLOOM_PLOTS_DIR"):
        render_and_save(_make_fig(), prefix="x", namespace="cyl_supabase")


def test_missing_plots_url_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOOM_PLOTS_DIR", str(tmp_path))
    monkeypatch.delenv("BLOOM_PLOTS_URL", raising=False)

    with pytest.raises(RuntimeError, match="BLOOM_PLOTS_URL"):
        render_and_save(_make_fig(), prefix="x", namespace="cyl_supabase")
