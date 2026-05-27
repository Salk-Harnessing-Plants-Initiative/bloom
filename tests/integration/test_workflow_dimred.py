"""Integration tests for run_dimensionality_reduction_workflow — PCA + UMAP
dispatch, versioned writes, response shape, error paths."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

_BLOOMMCP_DIR = Path(__file__).resolve().parents[2] / "bloommcp"
if str(_BLOOMMCP_DIR) not in sys.path:
    sys.path.insert(0, str(_BLOOMMCP_DIR))

_TMP_BASE = tempfile.mkdtemp(prefix="bloommcp_workflow_dimred_default_")
os.environ.setdefault("BLOOM_TRAITS_DIR", _TMP_BASE)
os.environ.setdefault("BLOOM_OUTPUT_DIR", _TMP_BASE)
os.environ.setdefault("BLOOM_PLOTS_DIR", _TMP_BASE)
os.environ.setdefault("BLOOM_PLOTS_URL", "http://test.invalid/plots")

from storage import read_manifest  # noqa: E402


def _seed_experiment(traits_dir: Path, name: str = "bar.csv", n_samples: int = 60, n_traits: int = 6):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("numpy")
    import numpy as np

    rng = np.random.default_rng(42)
    data = {
        "scan_id": [f"S{i}" for i in range(n_samples)],
        "genotype": [f"G{i % 6}" for i in range(n_samples)],
    }
    for i in range(n_traits):
        data[f"trait_{i}"] = rng.normal(loc=i, scale=1.0, size=n_samples)
    pd.DataFrame(data).to_csv(traits_dir / name, index=False)


def _setup_dirs(tmp_path: Path, monkeypatch) -> tuple[Path, Path, Path]:
    traits_dir = tmp_path / "traits"
    output_dir = tmp_path / "output"
    plots_dir = tmp_path / "plots"
    traits_dir.mkdir()
    output_dir.mkdir()
    plots_dir.mkdir()
    monkeypatch.setenv("BLOOM_OUTPUT_DIR", str(output_dir))
    monkeypatch.setenv("BLOOM_PLOTS_DIR", str(plots_dir))

    import source.experiment_utils as eu
    monkeypatch.setattr(eu, "TRAITS_DIR", traits_dir)
    monkeypatch.setattr(eu, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(eu, "PLOTS_DIR", plots_dir)
    return traits_dir, output_dir, plots_dir


# ===================== PCA =====================


def test_run_dimred_pca_returns_workflow_response_shape(tmp_path, monkeypatch):
    pytest.importorskip("sklearn")
    pytest.importorskip("scipy")
    traits_dir, output_dir, plots_dir = _setup_dirs(tmp_path, monkeypatch)
    _seed_experiment(traits_dir)

    from tools.workflows.dimred import run_dimensionality_reduction_workflow

    result = run_dimensionality_reduction_workflow("bar.csv", method="pca")

    for key in ("version_id", "version_dir", "manifest_path", "summary", "outputs", "plot_url", "plot_layout"):
        assert key in result, f"missing required key: {key}"
    assert result["version_id"] == "v1"
    assert "dimred_bar" in result["version_dir"]
    assert result["summary"]["method"] == "pca"
    assert "n_components_used" in result["summary"]
    assert "variance_explained_per_pc" in result["summary"]
    assert result["plot_layout"] == "scree"


def test_run_dimred_pca_writes_loadings_and_scores_csvs(tmp_path, monkeypatch):
    pytest.importorskip("sklearn")
    pytest.importorskip("scipy")
    traits_dir, output_dir, plots_dir = _setup_dirs(tmp_path, monkeypatch)
    _seed_experiment(traits_dir)

    from tools.workflows.dimred import run_dimensionality_reduction_workflow

    result = run_dimensionality_reduction_workflow("bar.csv", method="pca")
    version_dir = Path(result["version_dir"])

    for expected_file in (
        "pca_loadings.csv",
        "pca_variance_explained.csv",
        "pca_transformed_data.csv",
        "trait_variance_contrib.csv",
        "feature_metrics.csv",
    ):
        assert (version_dir / expected_file).exists(), f"missing PCA output: {expected_file}"
        assert expected_file in result["outputs"]


def test_run_dimred_pca_scree_plot_exists_at_url_path(tmp_path, monkeypatch):
    pytest.importorskip("sklearn")
    pytest.importorskip("scipy")
    pytest.importorskip("matplotlib")
    traits_dir, output_dir, plots_dir = _setup_dirs(tmp_path, monkeypatch)
    _seed_experiment(traits_dir)

    from tools.workflows.dimred import run_dimensionality_reduction_workflow

    result = run_dimensionality_reduction_workflow("bar.csv", method="pca")
    assert result["plot_url"].endswith(".png")
    plot_filename = result["plot_url"].rsplit("/", 1)[-1]
    assert (plots_dir / plot_filename).exists()


def test_run_dimred_pca_explicit_n_components(tmp_path, monkeypatch):
    pytest.importorskip("sklearn")
    pytest.importorskip("scipy")
    traits_dir, output_dir, plots_dir = _setup_dirs(tmp_path, monkeypatch)
    _seed_experiment(traits_dir, n_traits=10)

    from tools.workflows.dimred import run_dimensionality_reduction_workflow

    result = run_dimensionality_reduction_workflow("bar.csv", method="pca", n_components=3)
    assert result["summary"]["n_components_used"] == 3


def test_run_dimred_pca_writes_versioned_manifest_entry(tmp_path, monkeypatch):
    pytest.importorskip("sklearn")
    pytest.importorskip("scipy")
    traits_dir, output_dir, plots_dir = _setup_dirs(tmp_path, monkeypatch)
    _seed_experiment(traits_dir)

    from tools.workflows.dimred import run_dimensionality_reduction_workflow

    run_dimensionality_reduction_workflow("bar.csv", method="pca")
    dimred_dir = output_dir / "dimred_bar"
    manifest = read_manifest(dimred_dir)
    assert manifest is not None
    assert len(manifest.versions) == 1
    assert manifest.versions[0].tool == "run_dimensionality_reduction_workflow"
    assert manifest.versions[0].params["method"] == "pca"


# ===================== UMAP =====================


def test_run_dimred_umap_returns_workflow_response_shape(tmp_path, monkeypatch):
    pytest.importorskip("umap")
    pytest.importorskip("sklearn")
    traits_dir, output_dir, plots_dir = _setup_dirs(tmp_path, monkeypatch)
    _seed_experiment(traits_dir, n_samples=80)

    from tools.workflows.dimred import run_dimensionality_reduction_workflow

    result = run_dimensionality_reduction_workflow("bar.csv", method="umap")

    for key in ("version_id", "version_dir", "summary", "outputs", "plot_url", "plot_layout"):
        assert key in result
    assert result["summary"]["method"] == "umap"
    assert result["plot_layout"] == "scatter"
    assert "umap_embedding.csv" in result["outputs"]


def test_run_dimred_umap_writes_embedding_csv(tmp_path, monkeypatch):
    pytest.importorskip("umap")
    pytest.importorskip("sklearn")
    traits_dir, output_dir, plots_dir = _setup_dirs(tmp_path, monkeypatch)
    _seed_experiment(traits_dir, n_samples=80)

    from tools.workflows.dimred import run_dimensionality_reduction_workflow

    result = run_dimensionality_reduction_workflow("bar.csv", method="umap")
    version_dir = Path(result["version_dir"])
    emb_csv = version_dir / "umap_embedding.csv"
    assert emb_csv.exists()

    import pandas as pd
    df = pd.read_csv(emb_csv)
    assert set(df.columns) == {"UMAP1", "UMAP2"}
    assert len(df) == 80


def test_run_dimred_umap_param_passthrough(tmp_path, monkeypatch):
    pytest.importorskip("umap")
    pytest.importorskip("sklearn")
    traits_dir, output_dir, plots_dir = _setup_dirs(tmp_path, monkeypatch)
    _seed_experiment(traits_dir, n_samples=60)

    from tools.workflows.dimred import run_dimensionality_reduction_workflow

    result = run_dimensionality_reduction_workflow(
        "bar.csv", method="umap", n_neighbors=10, min_dist=0.25,
    )
    assert result["summary"]["n_neighbors"] == 10
    assert result["summary"]["min_dist"] == 0.25
    dimred_dir = output_dir / "dimred_bar"
    manifest = read_manifest(dimred_dir)
    assert manifest.versions[0].params["n_neighbors"] == 10
    assert manifest.versions[0].params["min_dist"] == 0.25


# ===================== Error paths =====================


def test_run_dimred_invalid_method_returns_error(tmp_path, monkeypatch):
    pytest.importorskip("pandas")
    _setup_dirs(tmp_path, monkeypatch)

    from tools.workflows.dimred import run_dimensionality_reduction_workflow

    result = run_dimensionality_reduction_workflow("bar.csv", method="tsne")
    assert "error" in result
    assert "Unknown method" in result["error"]
    assert "version_id" not in result


def test_run_dimred_missing_file_returns_error(tmp_path, monkeypatch):
    pytest.importorskip("pandas")
    _setup_dirs(tmp_path, monkeypatch)

    from tools.workflows.dimred import run_dimensionality_reduction_workflow

    result = run_dimensionality_reduction_workflow("does_not_exist.csv", method="pca")
    assert "error" in result
    assert "version_id" not in result


def test_run_dimred_pca_second_run_creates_v2(tmp_path, monkeypatch):
    pytest.importorskip("sklearn")
    pytest.importorskip("scipy")
    traits_dir, output_dir, plots_dir = _setup_dirs(tmp_path, monkeypatch)
    _seed_experiment(traits_dir)

    from tools.workflows.dimred import run_dimensionality_reduction_workflow

    r1 = run_dimensionality_reduction_workflow("bar.csv", method="pca")
    r2 = run_dimensionality_reduction_workflow("bar.csv", method="pca", n_components=2)
    assert r1["version_id"] == "v1"
    assert r2["version_id"] == "v2"
    dimred_dir = output_dir / "dimred_bar"
    manifest = read_manifest(dimred_dir)
    assert len(manifest.versions) == 2
    assert manifest.latest == "v2"
