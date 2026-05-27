"""Integration tests for run_outlier_workflow — versioned writes, response shape,
method dispatch (single-detector / consensus / all_then_consensus), and the
optional outlier-removal step."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

_BLOOMMCP_DIR = Path(__file__).resolve().parents[2] / "bloommcp"
if str(_BLOOMMCP_DIR) not in sys.path:
    sys.path.insert(0, str(_BLOOMMCP_DIR))

_TMP_BASE = tempfile.mkdtemp(prefix="bloommcp_workflow_outlier_default_")
os.environ.setdefault("BLOOM_TRAITS_DIR", _TMP_BASE)
os.environ.setdefault("BLOOM_OUTPUT_DIR", _TMP_BASE)
os.environ.setdefault("BLOOM_PLOTS_DIR", _TMP_BASE)
os.environ.setdefault("BLOOM_PLOTS_URL", "http://test.invalid")

from storage import read_manifest  # noqa: E402


def _seed_experiment(traits_dir: Path, name: str = "bar.csv", n: int = 50):
    """Write a small experiment CSV with `n` samples and 3 numeric traits."""
    pd = pytest.importorskip("pandas")
    pytest.importorskip("numpy")
    import numpy as np

    rng = np.random.default_rng(42)
    df = pd.DataFrame(
        {
            "scan_id": [f"S{i}" for i in range(n)],
            "genotype": ["A"] * (n // 2) + ["B"] * (n - n // 2),
            "trait_a": rng.normal(size=n),
            "trait_b": rng.normal(size=n),
            "trait_c": rng.normal(size=n),
        }
    )
    df.to_csv(traits_dir / name, index=False)


def _setup_dirs(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    traits_dir = tmp_path / "traits"
    output_dir = tmp_path / "output"
    traits_dir.mkdir()
    output_dir.mkdir()
    monkeypatch.setenv("BLOOM_OUTPUT_DIR", str(output_dir))

    import source.experiment_utils as eu
    monkeypatch.setattr(eu, "TRAITS_DIR", traits_dir)
    monkeypatch.setattr(eu, "OUTPUT_DIR", output_dir)
    return traits_dir, output_dir


def test_run_outlier_workflow_pca_returns_workflow_response_shape(tmp_path, monkeypatch):
    pytest.importorskip("sklearn")
    traits_dir, output_dir = _setup_dirs(tmp_path, monkeypatch)
    _seed_experiment(traits_dir)

    from tools.workflows.outlier import run_outlier_workflow

    result = run_outlier_workflow("bar.csv", method="pca")

    for key in ("version_id", "version_dir", "manifest_path", "summary", "outputs"):
        assert key in result, f"missing required key: {key}"
    assert result["version_id"] == "v1"
    assert "outlier_bar" in result["version_dir"]
    assert result["summary"]["method"] == "pca"
    assert "n_outliers" in result["summary"]
    assert "pca_outliers.json" in result["outputs"]


def test_run_outlier_workflow_mahalanobis_writes_versioned(tmp_path, monkeypatch):
    pytest.importorskip("sklearn")
    traits_dir, output_dir = _setup_dirs(tmp_path, monkeypatch)
    _seed_experiment(traits_dir)

    from tools.workflows.outlier import run_outlier_workflow

    result = run_outlier_workflow("bar.csv", method="mahalanobis")
    outlier_dir = output_dir / "outlier_bar"
    assert outlier_dir.exists()
    manifest = read_manifest(outlier_dir)
    assert manifest is not None
    assert len(manifest.versions) == 1
    assert manifest.versions[0].tool == "run_outlier_workflow"
    assert manifest.versions[0].params["method"] == "mahalanobis"


def test_run_outlier_workflow_all_then_consensus_produces_single_version(tmp_path, monkeypatch):
    """`method='all_then_consensus'` runs 3 detectors but commits ONE version with per-detector counts inlined."""
    pytest.importorskip("sklearn")
    traits_dir, output_dir = _setup_dirs(tmp_path, monkeypatch)
    _seed_experiment(traits_dir)

    from tools.workflows.outlier import run_outlier_workflow

    result = run_outlier_workflow("bar.csv", method="all_then_consensus", remove_outliers=False)
    outlier_dir = output_dir / "outlier_bar"
    manifest = read_manifest(outlier_dir)

    assert len(manifest.versions) == 1
    assert "per_detector" in result["summary"]
    pd_counts = result["summary"]["per_detector"]
    assert "mahalanobis" in pd_counts
    assert "isolation_forest" in pd_counts
    assert "pca" in pd_counts
    # All 4 detection JSONs (consensus + 3 individual) written
    assert "consensus_outliers.json" in result["outputs"]
    assert "mahalanobis_outliers.json" in result["outputs"]
    assert "isolation_forest_outliers.json" in result["outputs"]
    assert "pca_outliers.json" in result["outputs"]


def test_run_outlier_workflow_consensus_default_keeps_only_consensus_json(tmp_path, monkeypatch):
    """Plain `method='consensus'` writes only the consensus JSON, not per-detector ones."""
    pytest.importorskip("sklearn")
    traits_dir, output_dir = _setup_dirs(tmp_path, monkeypatch)
    _seed_experiment(traits_dir)

    from tools.workflows.outlier import run_outlier_workflow

    result = run_outlier_workflow("bar.csv", method="consensus", remove_outliers=False)
    assert "consensus_outliers.json" in result["outputs"]
    assert "mahalanobis_outliers.json" not in result["outputs"]
    assert "isolation_forest_outliers.json" not in result["outputs"]
    assert "pca_outliers.json" not in result["outputs"]


def test_run_outlier_workflow_remove_outliers_writes_cleaned_csv(tmp_path, monkeypatch):
    pytest.importorskip("sklearn")
    traits_dir, output_dir = _setup_dirs(tmp_path, monkeypatch)
    _seed_experiment(traits_dir, n=100)  # bigger sample so detectors find at least 1

    from tools.workflows.outlier import run_outlier_workflow

    result = run_outlier_workflow(
        "bar.csv", method="mahalanobis", chi2_percentile=80.0, remove_outliers=True,
    )
    # At the 80th percentile, Mahalanobis should flag at least one outlier
    cleaned_key = "bar_cleaned.csv"
    if result["summary"].get("n_outliers", 0) > 0:
        assert cleaned_key in result["outputs"]
        assert "bar_outlier_samples.csv" in result["outputs"]
        assert result["summary"]["n_remaining"] < result["summary"]["n_original_samples"]
        version_dir = Path(result["version_dir"])
        assert (version_dir / cleaned_key).exists()


def test_run_outlier_workflow_remove_outliers_false_skips_cleaned_csv(tmp_path, monkeypatch):
    pytest.importorskip("sklearn")
    traits_dir, output_dir = _setup_dirs(tmp_path, monkeypatch)
    _seed_experiment(traits_dir)

    from tools.workflows.outlier import run_outlier_workflow

    result = run_outlier_workflow("bar.csv", method="pca", remove_outliers=False)
    assert "bar_cleaned.csv" not in result["outputs"]
    assert "bar_outlier_samples.csv" not in result["outputs"]


def test_run_outlier_workflow_invalid_method_returns_error(tmp_path, monkeypatch):
    pytest.importorskip("pandas")
    _setup_dirs(tmp_path, monkeypatch)

    from tools.workflows.outlier import run_outlier_workflow

    result = run_outlier_workflow("bar.csv", method="bogus")
    assert "error" in result
    assert "Unknown method" in result["error"]
    assert "version_id" not in result


def test_run_outlier_workflow_missing_file_returns_error(tmp_path, monkeypatch):
    pytest.importorskip("pandas")
    _setup_dirs(tmp_path, monkeypatch)

    from tools.workflows.outlier import run_outlier_workflow

    result = run_outlier_workflow("does_not_exist.csv", method="pca")
    assert "error" in result
    assert "version_id" not in result


def test_run_outlier_workflow_second_run_creates_v2(tmp_path, monkeypatch):
    pytest.importorskip("sklearn")
    traits_dir, output_dir = _setup_dirs(tmp_path, monkeypatch)
    _seed_experiment(traits_dir)

    from tools.workflows.outlier import run_outlier_workflow

    r1 = run_outlier_workflow("bar.csv", method="pca", remove_outliers=False)
    r2 = run_outlier_workflow("bar.csv", method="mahalanobis", remove_outliers=False)
    assert r1["version_id"] == "v1"
    assert r2["version_id"] == "v2"
    manifest = read_manifest(output_dir / "outlier_bar")
    assert len(manifest.versions) == 2
    assert manifest.latest == "v2"
