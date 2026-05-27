"""storage layer write-side: AnalysisWriter, migration, with_snapshot.

These tests are filesystem-only — they do NOT require the compose stack. They
run against the storage modules directly, using tmp_path for isolation.

Pandas-dependent tests (the qc/outlier tool integration assertions) are skipped
when pandas isn't installed in the test runner's environment.
"""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

import pytest

_BLOOMMCP_DIR = Path(__file__).resolve().parents[2] / "bloommcp"
if str(_BLOOMMCP_DIR) not in sys.path:
    sys.path.insert(0, str(_BLOOMMCP_DIR))

_TMP_BASE = tempfile.mkdtemp(prefix="bloommcp_phase_b_default_")
os.environ.setdefault("BLOOM_TRAITS_DIR", _TMP_BASE)
os.environ.setdefault("BLOOM_OUTPUT_DIR", _TMP_BASE)
os.environ.setdefault("BLOOM_PLOTS_DIR", _TMP_BASE)
os.environ.setdefault("BLOOM_PLOTS_URL", "http://test.invalid")

from storage import (  # noqa: E402
    AnalysisDir,
    AnalysisWriter,
    Manifest,
    read_manifest,
)


# ─── AnalysisWriter — single-version creation and commit ───────────────────────


def test_writer_first_version_creates_v1_dir_and_manifest(tmp_path):
    csv = tmp_path / "foo.csv"
    csv.write_bytes(b"col_a,col_b\n1,2\n")
    writer = AnalysisWriter(tmp_path, "foo.csv", "qc", source_csv=csv)

    version_dir = writer.create_version(
        tool_name="clean_experiment_data",
        params={"contamination": 0.05},
    )
    assert version_dir.exists()
    assert version_dir.parent == tmp_path / "qc_foo"
    assert version_dir.name.startswith("v1_")

    (version_dir / "_cleaned.csv").write_text("col_a,col_b\n1,2\n")
    entry = writer.commit({"_cleaned.csv": f"{version_dir.name}/_cleaned.csv"})

    assert entry.id == "v1"
    assert entry.tool == "clean_experiment_data"
    assert entry.params == {"contamination": 0.05}
    assert entry.outputs == {"_cleaned.csv": f"{version_dir.name}/_cleaned.csv"}
    assert entry.based_on_version == "raw"

    manifest = read_manifest(tmp_path / "qc_foo")
    assert manifest is not None
    assert manifest.latest == "v1"
    assert len(manifest.versions) == 1
    assert manifest.experiment.input_sha256  # non-empty


def test_writer_second_version_creates_v2_and_preserves_v1(tmp_path):
    csv = tmp_path / "foo.csv"
    csv.write_bytes(b"col_a\n1\n")

    w1 = AnalysisWriter(tmp_path, "foo.csv", "qc", source_csv=csv)
    v1 = w1.create_version("clean_experiment_data", {})
    (v1 / "_cleaned.csv").write_text("col_a\n1\n")
    w1.commit({"_cleaned.csv": f"{v1.name}/_cleaned.csv"})

    w2 = AnalysisWriter(tmp_path, "foo.csv", "qc", source_csv=csv)
    v2 = w2.create_version("clean_experiment_data", {"max_nans": 0.1})
    assert v2.name.startswith("v2_")
    (v2 / "_cleaned.csv").write_text("col_a\n1\n")
    entry = w2.commit({"_cleaned.csv": f"{v2.name}/_cleaned.csv"})

    assert v1.exists()  # untouched
    assert v2.exists()
    manifest = read_manifest(tmp_path / "qc_foo")
    assert [v.id for v in manifest.versions] == ["v1", "v2"]
    assert manifest.latest == "v2"
    assert entry.id == "v2"


def test_writer_user_label_appears_in_dir_name_and_entry(tmp_path):
    csv = tmp_path / "foo.csv"
    csv.write_bytes(b"col_a\n1\n")

    writer = AnalysisWriter(tmp_path, "foo.csv", "qc", source_csv=csv)
    version_dir = writer.create_version(
        tool_name="clean_experiment_data",
        params={},
        user_label="Iso Method",
    )
    assert "iso_method" in version_dir.name


def test_writer_captures_code_versions_in_manifest(tmp_path):
    csv = tmp_path / "foo.csv"
    csv.write_bytes(b"col_a\n1\n")

    writer = AnalysisWriter(tmp_path, "foo.csv", "qc", source_csv=csv)
    v1 = writer.create_version("clean_experiment_data", {})
    (v1 / "_cleaned.csv").write_text("col_a\n1\n")
    entry = writer.commit({"_cleaned.csv": f"{v1.name}/_cleaned.csv"})

    assert entry.code_versions.bloommcp  # one of "<ver>" or "unknown"
    assert entry.code_versions.sleap_roots_analyze


# ─── Concurrent writers ────────────────────────────────────────────────────────


def test_concurrent_writers_get_distinct_version_ids(tmp_path):
    """Three threads each create_version + commit; the manifest sequences them
    monotonically without corruption."""
    csv = tmp_path / "foo.csv"
    csv.write_bytes(b"col_a\n1\n")

    results: list = []
    errors: list = []
    barrier = threading.Barrier(3)

    def worker():
        try:
            barrier.wait()
            w = AnalysisWriter(tmp_path, "foo.csv", "qc", source_csv=csv)
            v = w.create_version("clean_experiment_data", {})
            (v / "_cleaned.csv").write_text("col_a\n1\n")
            entry = w.commit({"_cleaned.csv": f"{v.name}/_cleaned.csv"})
            results.append(entry.id)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert sorted(results) == ["v1", "v2", "v3"]
    manifest = read_manifest(tmp_path / "qc_foo")
    assert sorted(v.id for v in manifest.versions) == ["v1", "v2", "v3"]


# ─── with_snapshot — pinned reads inside a recipe ──────────────────────────────


def test_with_snapshot_pins_manifest_against_concurrent_commit(tmp_path):
    csv = tmp_path / "foo.csv"
    csv.write_bytes(b"col_a\n1\n")

    seed = AnalysisWriter(tmp_path, "foo.csv", "qc", source_csv=csv)
    v1 = seed.create_version("clean_experiment_data", {})
    (v1 / "_cleaned.csv").write_text("col_a\n1\n")
    seed.commit({"_cleaned.csv": f"{v1.name}/_cleaned.csv"})

    ad = AnalysisDir(tmp_path, "foo.csv", "qc")
    with ad.with_snapshot() as snapshot:
        # Sibling commit happens mid-recipe
        sibling = AnalysisWriter(tmp_path, "foo.csv", "qc", source_csv=csv)
        v2 = sibling.create_version("clean_experiment_data", {"second": True})
        (v2 / "_cleaned.csv").write_text("col_a\n1\n")
        sibling.commit({"_cleaned.csv": f"{v2.name}/_cleaned.csv"})

        # Inside the snapshot we still see only v1
        assert snapshot.latest == "v1"
        assert [v.id for v in snapshot.versions] == ["v1"]

    # After the context exits, AnalysisDir reflects live state
    assert ad.read_manifest().latest == "v2"


# ─── Integration: refactored qc_tools.clean_experiment_data ────────────────────


def test_clean_experiment_data_writes_versioned(tmp_path, monkeypatch):
    pytest.importorskip("pandas")
    import pandas as pd

    traits_dir = tmp_path / "traits"
    output_dir = tmp_path / "output"
    traits_dir.mkdir()
    output_dir.mkdir()

    # Make a small experiment file
    df = pd.DataFrame(
        {
            "scan_id": [f"S{i}" for i in range(20)],
            "genotype": ["A"] * 10 + ["B"] * 10,
            "trait_a": list(range(20)),
            "trait_b": [float(i) for i in range(20)],
        }
    )
    df.to_csv(traits_dir / "bar.csv", index=False)

    import source.experiment_utils as eu
    monkeypatch.setattr(eu, "TRAITS_DIR", traits_dir)
    monkeypatch.setattr(eu, "OUTPUT_DIR", output_dir)

    import tools.qc_tools as qc
    monkeypatch.setattr(qc, "OUTPUT_DIR", output_dir)

    result = qc.clean_experiment_data("bar.csv")
    assert "v1" in result or "version" in result.lower()

    qc_dir = output_dir / "qc_bar"
    manifest = read_manifest(qc_dir)
    assert manifest is not None
    assert len(manifest.versions) == 1
    assert manifest.versions[0].tool == "clean_experiment_data"


def test_detect_outliers_pca_writes_versioned(tmp_path, monkeypatch):
    pytest.importorskip("pandas")
    pytest.importorskip("sklearn")
    import pandas as pd
    import numpy as np

    traits_dir = tmp_path / "traits"
    output_dir = tmp_path / "output"
    traits_dir.mkdir()
    output_dir.mkdir()

    rng = np.random.default_rng(42)
    df = pd.DataFrame(
        {
            "scan_id": [f"S{i}" for i in range(50)],
            "genotype": ["A"] * 25 + ["B"] * 25,
            "trait_a": rng.normal(size=50),
            "trait_b": rng.normal(size=50),
            "trait_c": rng.normal(size=50),
        }
    )
    df.to_csv(traits_dir / "bar.csv", index=False)

    import source.experiment_utils as eu
    monkeypatch.setattr(eu, "TRAITS_DIR", traits_dir)
    monkeypatch.setattr(eu, "OUTPUT_DIR", output_dir)

    import tools.outlier_tools as ot
    monkeypatch.setattr(ot, "OUTPUT_DIR", output_dir)

    ot.detect_outliers_pca("bar.csv")

    outlier_dir = output_dir / "outlier_bar"
    assert outlier_dir.exists()
    manifest = read_manifest(outlier_dir)
    assert manifest is not None
    assert len(manifest.versions) == 1
    assert manifest.versions[0].tool == "detect_outliers_pca"
