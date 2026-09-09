"""
Unit tests for `scripts/ingest_scrnaseq.py`.

The script's job before it writes anything is to refuse a file it cannot load
honestly, because a half-ingested dataset is indistinguishable to the explorer
from a complete one. These tests are mostly about that refusal: every check runs
against a real `.h5ad` written to a temporary file, so a change to the reader
that stops rejecting is caught here rather than by a bad dataset appearing in
the UI.

The database writes are covered by the integration suite, which has a database.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "ingest_scrnaseq.py"

import anndata
import numpy as np
import pandas as pd


@pytest.fixture(scope="module")
def ingest():
    spec = importlib.util.spec_from_file_location("ingest_scrnaseq", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["ingest_scrnaseq"] = module
    spec.loader.exec_module(module)
    return module


def write_h5ad(
    path: Path,
    n_cells: int = 6,
    umap_key: str = "X_umap",
    umap_dims: int = 2,
    annotation: str = "nn_label_plain",
    sample_column: str = "sample",
    labels: list[str] | None = None,
    n_types: int = 2,
) -> Path:
    """A miniature dataset shaped like the real one."""
    obs = pd.DataFrame(
        {
            annotation: labels or [
                f"Type{i % n_types}" for i in range(n_cells)
            ] if n_types != 2 else (labels or ["Phellem", "Cortex"] * (n_cells // 2)),
            sample_column: ["Col-0", "pFACT", "pHORST"] * (n_cells // 3),
        },
        index=[f"CELL{i}-Col-0" for i in range(n_cells)],
    )
    adata = anndata.AnnData(
        X=np.zeros((n_cells, 4), dtype="float32"),
        obs=obs,
        var=pd.DataFrame(index=[f"AT1G0{i}" for i in range(4)]),
    )
    if umap_key:
        # Cells of a type sit together, as they do in a real embedding, so the
        # alignment check passes; distinct x and y so a swap is detectable.
        codes = pd.Categorical(obs[annotation]).codes.astype("float32")
        base = np.stack([codes * 100.0, codes * 100.0 + 50.0], axis=1)
        jitter = np.linspace(0, 1, n_cells, dtype="float32")[:, None]
        coords = (base + jitter)[:, :umap_dims] if umap_dims <= 2 else np.hstack(
            [base, np.zeros((n_cells, umap_dims - 2), dtype="float32")]
        )
        adata.obsm[umap_key] = coords.astype("float32")
    adata.write_h5ad(path)
    return path


# --------------------------------------------------------------------------- #
# What it accepts
# --------------------------------------------------------------------------- #


def test_reads_a_well_formed_file(ingest, tmp_path):
    cells = ingest.read_cells(
        write_h5ad(tmp_path / "ok.h5ad"), "nn_label_plain", "sample", "X_umap", None
    )
    assert cells["n_cells"] == 6
    assert cells["n_genes"] == 4
    assert len(cells["x"]) == 6 and len(cells["y"]) == 6
    assert cells["levels"] == ["Cortex", "Phellem"]
    assert set(cells["samples"]) == {"Col-0", "pFACT", "pHORST"}


def test_levels_are_sorted_so_ordinals_are_stable(ingest, tmp_path):
    """The same file must produce the same catalogue every time, or a cell type
    changes colour between loads."""
    path = write_h5ad(
        tmp_path / "order.h5ad", n_cells=6,
        labels=["Xylem", "Cortex", "Phellem", "Cortex", "Xylem", "Phellem"],
    )
    first = ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)
    second = ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)
    assert first["levels"] == second["levels"] == ["Cortex", "Phellem", "Xylem"]


def test_expected_cell_count_passes_when_it_matches(ingest, tmp_path):
    ingest.read_cells(
        write_h5ad(tmp_path / "count.h5ad"), "nn_label_plain", "sample", "X_umap", 6
    )


# --------------------------------------------------------------------------- #
# What it refuses, and why
# --------------------------------------------------------------------------- #


def test_missing_coordinates_are_refused(ingest, tmp_path):
    """The explorer plots stored coordinates and never computes them, so a file
    without them cannot be shown at all."""
    path = write_h5ad(tmp_path / "nocoords.h5ad", umap_key="")
    with pytest.raises(ingest.IngestError, match="X_umap"):
        ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_one_dimensional_coordinates_are_refused(ingest, tmp_path):
    path = write_h5ad(tmp_path / "onedim.h5ad", umap_dims=1)
    with pytest.raises(ingest.IngestError, match="need exactly"):
        ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_a_high_dimensional_embedding_is_refused(ingest, tmp_path):
    """A 50-column X_pca must not load as a UMAP -- it is the obvious
    workaround when the real coordinates are missing, and the resulting plot is
    indistinguishable from a real one."""
    path = write_h5ad(tmp_path / "pca.h5ad", umap_key="X_pca", umap_dims=50)
    with pytest.raises(ingest.IngestError, match="need exactly 2"):
        ingest.read_cells(path, "nn_label_plain", "sample", "X_pca", None)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")],
                         ids=["nan", "inf", "-inf"])
def test_non_finite_coordinates_are_refused(ingest, tmp_path, bad):
    path = tmp_path / f"nonfinite{bad}.h5ad"
    write_h5ad(path)
    a = anndata.read_h5ad(path)
    coords = np.asarray(a.obsm["X_umap"]).copy()
    coords[1, 0] = bad
    a.obsm["X_umap"] = coords
    a.write_h5ad(path)
    with pytest.raises(ingest.IngestError, match="finite"):
        ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_a_missing_label_is_refused_not_stored_as_nan(ingest, tmp_path):
    """str(NaN) is "nan", which would become a real cell type in the legend and
    merge with any genuine level of that spelling."""
    path = tmp_path / "nanlabel.h5ad"
    write_h5ad(path)
    a = anndata.read_h5ad(path)
    a.obs["nn_label_plain"] = pd.Categorical(
        ["Phellem", None, "Phellem", "Cortex", "Cortex", "Cortex"]
    )
    a.write_h5ad(path)
    with pytest.raises(ingest.IngestError, match="missing value"):
        ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_a_blank_label_is_refused(ingest, tmp_path):
    path = tmp_path / "blank.h5ad"
    write_h5ad(path)
    a = anndata.read_h5ad(path)
    a.obs["nn_label_plain"] = ["Phellem", "   ", "Phellem", "Cortex", "Cortex", "Cortex"]
    a.write_h5ad(path)
    with pytest.raises(ingest.IngestError, match="blank"):
        ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_a_file_with_no_cells_is_refused(ingest, tmp_path):
    """Otherwise it would wipe a loaded dataset and replace it with nothing."""
    path = tmp_path / "empty.h5ad"
    anndata.AnnData(
        X=np.zeros((0, 4), dtype="float32"),
        obs=pd.DataFrame({"nn_label_plain": [], "sample": []}),
        var=pd.DataFrame(index=[f"AT1G0{i}" for i in range(4)]),
    ).write_h5ad(path)
    with pytest.raises(ingest.IngestError, match="no cells"):
        ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_coordinates_that_do_not_match_the_cells_are_refused(ingest, tmp_path):
    """The load requires someone to slice this dataset's rows out of a much
    larger joint embedding. A wrong slice passes every other check."""
    path = tmp_path / "shuffled.h5ad"
    write_h5ad(path, n_cells=120, n_types=8)
    a = anndata.read_h5ad(path)
    coords = np.asarray(a.obsm["X_umap"]).copy()
    a.obsm["X_umap"] = coords[np.random.default_rng(0).permutation(len(coords))]
    a.write_h5ad(path)
    with pytest.raises(ingest.IngestError, match="do not line up"):
        ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_well_clustered_coordinates_are_accepted(ingest, tmp_path):
    cells = ingest.read_cells(
        write_h5ad(tmp_path / "clustered.h5ad", n_cells=120, n_types=8),
        "nn_label_plain", "sample", "X_umap", None,
    )
    assert cells["purity"] > 0.5


def test_missing_annotation_column_is_refused(ingest, tmp_path):
    path = write_h5ad(tmp_path / "noann.h5ad")
    with pytest.raises(ingest.IngestError, match="saturn_Celltype"):
        ingest.read_cells(path, "saturn_Celltype", "sample", "X_umap", None)


def test_missing_sample_column_is_refused(ingest, tmp_path):
    path = write_h5ad(tmp_path / "nosample.h5ad")
    with pytest.raises(ingest.IngestError, match="genotype"):
        ingest.read_cells(path, "nn_label_plain", "genotype", "X_umap", None)


def test_unexpected_cell_count_is_refused(ingest, tmp_path):
    """Guards against loading a file that is not the one that was reviewed."""
    path = write_h5ad(tmp_path / "shortcount.h5ad", n_cells=6)
    with pytest.raises(ingest.IngestError, match="expected 8683"):
        ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", 8683)


def test_too_many_levels_for_the_ordinal_is_refused(ingest, tmp_path):
    """The browser packs the ordinal into a byte and reserves 255 for orphans."""
    labels = [f"type{i}" for i in range(300)]
    path = write_h5ad(tmp_path / "manylevels.h5ad", n_cells=300, labels=labels)
    with pytest.raises(ingest.IngestError, match="orphans"):
        ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_a_missing_file_is_refused(ingest, tmp_path):
    with pytest.raises(ingest.IngestError, match="no such file"):
        ingest.read_cells(tmp_path / "absent.h5ad", "nn_label_plain",
                          "sample", "X_umap", None)


# --------------------------------------------------------------------------- #
# Supporting pieces
# --------------------------------------------------------------------------- #


def test_the_palette_has_a_distinct_colour_for_every_cell_type(ingest):
    """23 cell types in the target dataset, so 23 distinct colours -- a repeat
    renders two different cell types identically in the plot and the legend."""
    assert len(ingest.PALETTE) >= 23
    assert len(set(ingest.PALETTE)) == len(ingest.PALETTE)
    assert all(c.startswith("#") and len(c) == 7 for c in ingest.PALETTE)


def test_checksum_is_stable_and_content_dependent(ingest, tmp_path):
    a, b = tmp_path / "a.bin", tmp_path / "b.bin"
    a.write_bytes(b"same"); b.write_bytes(b"same")
    assert ingest.checksum(a) == ingest.checksum(b)
    b.write_bytes(b"different")
    assert ingest.checksum(a) != ingest.checksum(b)


def test_summary_names_the_samples_and_their_counts(ingest, tmp_path):
    cells = ingest.read_cells(
        write_h5ad(tmp_path / "sum.h5ad"), "nn_label_plain", "sample", "X_umap", None
    )
    text = ingest.summarise(cells)
    assert "6 cells" in text and "4 genes" in text
    for sample in ("Col-0", "pFACT", "pHORST"):
        assert sample in text


def test_dry_run_writes_nothing_and_needs_no_credentials(ingest, tmp_path, capsys):
    path = write_h5ad(tmp_path / "dry.h5ad")
    code = ingest.main([
        "--h5ad", str(path), "--dataset-name", "t",
        "--species-id", "1", "--annotation", "nn_label_plain", "--dry-run",
    ])
    assert code == 0
    assert "nothing written" in capsys.readouterr().out


def test_a_bad_file_exits_non_zero_without_touching_the_database(ingest, tmp_path, capsys):
    path = write_h5ad(tmp_path / "bad.h5ad", umap_key="")
    code = ingest.main([
        "--h5ad", str(path), "--dataset-name", "t",
        "--species-id", "1", "--annotation", "nn_label_plain",
    ])
    assert code == 1
    assert "refusing to ingest" in capsys.readouterr().err
