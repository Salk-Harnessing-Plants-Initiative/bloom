"""
Unit tests for `scripts/ingest_scrnaseq.py`.

The script's job before it writes anything is to refuse a file it cannot load
honestly, because a half-ingested dataset is indistinguishable to the explorer
from a complete one. These tests are mostly about that refusal: every check runs
against a real `.h5ad` written to a temporary file, so a change to the reader
that stops rejecting is caught here rather than by a bad dataset appearing in
the UI.

The write path is covered by tests/integration/test_scrna_ingest_cells.py, which
drives `load()` against a real database.
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
    samples: list[str] | None = None,
    coords: "np.ndarray | None" = None,
    barcodes: list[str] | None = None,
) -> Path:
    """A miniature dataset shaped like the real one."""
    obs = pd.DataFrame(
        {
            annotation: labels or ["Phellem", "Cortex"] * (n_cells // 2),
            sample_column: samples or ["Col-0", "pFACT", "pHORST"] * (n_cells // 3),
        },
        index=barcodes or [f"CELL{i}-Col-0" for i in range(n_cells)],
    )
    adata = anndata.AnnData(
        X=np.zeros((n_cells, 4), dtype="float32"),
        obs=obs,
        var=pd.DataFrame(index=[f"AT1G0{i}" for i in range(4)]),
    )
    if umap_key and coords is not None:
        adata.obsm[umap_key] = np.asarray(coords)
    elif umap_key:
        # distinct x and y for every cell, so a swap or a reversal is detectable.
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
    assert cells["levels"] == ["Cortex", "Phellem"]
    assert cells["samples"] == ["Col-0", "pFACT", "pHORST"] * 2

    # Values, not lengths. This is where a row-order mistake enters -- the
    # integration tests are handed a dict and cannot see it -- so x, y and the
    # barcodes are pinned against the fixture's own arithmetic. Checking only
    # the lengths let x and y swap, and the barcodes reverse, unnoticed.
    codes = [1, 0, 1, 0, 1, 0]                     # Phellem, Cortex, ...
    jitter = [i / 5 for i in range(6)]
    assert cells["x"] == pytest.approx([c * 100.0 + j for c, j in zip(codes, jitter)])
    assert cells["y"] == pytest.approx([c * 100.0 + 50.0 + j
                                        for c, j in zip(codes, jitter)])
    assert cells["barcodes"] == [f"CELL{i}-Col-0" for i in range(6)]


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


def test_as_many_cell_types_as_there_are_colours_is_accepted(ingest, tmp_path):
    n = len(ingest.PALETTE)
    labels = [f"type{i}" for i in range(n)] * 21
    path = write_h5ad(tmp_path / "exactly.h5ad", n_cells=n * 21, labels=labels)
    cells = ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)
    assert len(cells["levels"]) == n


def test_one_more_cell_type_than_colours_is_refused(ingest, tmp_path):
    """Wrapping the palette would draw two cell types identically. One column of
    the real file has exactly 24 levels, so this is one flag away."""
    n = len(ingest.PALETTE) + 1
    labels = [f"type{i}" for i in range(n)] * 21
    path = write_h5ad(tmp_path / "onemore.h5ad", n_cells=n * 21, labels=labels)
    with pytest.raises(ingest.IngestError, match="drawn identically"):
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
    assert len(ingest.PALETTE) == 23
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


def test_dry_run_writes_nothing_and_needs_no_credentials(ingest, tmp_path, capsys,
                                                         monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("a dry run must not reach the site")
    monkeypatch.setattr(ingest.ingest_api, "resolve_api", no_network)
    monkeypatch.setattr(ingest.ingest_api, "sign_in", no_network)
    monkeypatch.delenv("BLOOM_PASSWORD", raising=False)
    path = write_h5ad(tmp_path / "dry.h5ad")
    code = ingest.main([
        "--h5ad", str(path), "--dataset-name", "t",
        "--species-id", "1", "--annotation", "nn_label_plain", "--dry-run",
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "'t'" in out and "6 cells" in out and "2 cell types" in out
    assert "dry run — nothing written" in out


def test_a_bad_file_exits_non_zero_without_touching_the_database(ingest, tmp_path, capsys):
    path = write_h5ad(tmp_path / "bad.h5ad", umap_key="")
    code = ingest.main([
        "--h5ad", str(path), "--dataset-name", "t",
        "--species-id", "1", "--annotation", "nn_label_plain",
    ])
    assert code == 1
    assert "refusing to ingest" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# Coordinates that are present but not real
# --------------------------------------------------------------------------- #


def test_unfilled_coordinates_are_refused(ingest, tmp_path):
    """An obsm allocated and never filled is finite, 2-D and the right length,
    so every other check here passes it and the plot is a single dot."""
    labels = ["A", "B", "C", "D"] * 30
    path = write_h5ad(tmp_path / "zeros.h5ad", n_cells=120, labels=labels,
                      coords=np.zeros((120, 2)))
    with pytest.raises(ingest.IngestError, match="unfilled"):
        ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_partly_unfilled_coordinates_are_refused(ingest, tmp_path):
    """The realistic version: most of the array written, the tail left as
    zeros -- so the count of cells on one point is what has to catch it."""
    coords = np.vstack([np.array([[float(i), float(i)] for i in range(1080)]),
                        np.zeros((120, 2))])
    path = write_h5ad(tmp_path / "tail.h5ad", n_cells=1200,
                      labels=["A", "B", "C", "D"] * 300, coords=coords)
    with pytest.raises(ingest.IngestError, match="unfilled"):
        ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_coordinates_too_large_to_store_are_refused(ingest, tmp_path):
    """Finite, but larger than the REAL column the explorer reads."""
    labels = ["A", "B", "C", "D"] * 30
    coords = np.array([[float(i), 0.0] for i in range(120)], dtype=float)
    coords[7, 0] = 1e300
    path = write_h5ad(tmp_path / "huge.h5ad", n_cells=120, labels=labels,
                      coords=coords)
    with pytest.raises(ingest.IngestError, match="too large to store"):
        ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_more_cells_than_expected_is_refused(ingest, tmp_path):
    """Both directions, so narrowing the check to one of them fails here."""
    path = write_h5ad(tmp_path / "more.h5ad", n_cells=6)
    with pytest.raises(ingest.IngestError, match="expected 3 cells"):
        ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", 3)


def test_a_non_finite_value_in_either_column_is_refused(ingest, tmp_path):
    """The y column too, so the check cannot narrow to x and still pass."""
    for column in (0, 1):
        coords = np.array([[float(i), float(i) + 0.5] for i in range(120)])
        coords[5, column] = np.nan
        path = write_h5ad(tmp_path / f"nan{column}.h5ad", n_cells=120,
                          labels=["A", "B", "C", "D"] * 30, coords=coords)
        with pytest.raises(ingest.IngestError, match="not finite"):
            ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_three_dimensional_coordinates_are_refused(ingest, tmp_path):
    """A (n, 2, k) array has shape[1] == 2, so only the dimension count sees it."""
    coords = np.zeros((120, 2, 3))
    path = write_h5ad(tmp_path / "cube.h5ad", n_cells=120,
                      labels=["A", "B", "C", "D"] * 30, coords=coords)
    with pytest.raises(ingest.IngestError, match="3-dimensional"):
        ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_integer_coordinates_are_read_not_crashed_on(ingest, tmp_path):
    """An integer obsm used to raise OverflowError out of numpy rather than
    being read or refused."""
    coords = np.array([[(i % 4) * 1000 + i, 0] for i in range(120)], dtype="int64")
    path = write_h5ad(tmp_path / "ints.h5ad", n_cells=120,
                      labels=["A", "B", "C", "D"] * 30, coords=coords)
    cells = ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)
    assert cells["x"][:2] == pytest.approx([0.0, 1001.0])
def test_main_reads_the_annotation_the_operator_named(ingest, tmp_path, capsys):
    """Pins the reader wiring only -- a dry run stops before the database."""
    path = write_h5ad(tmp_path / "wiring.h5ad", n_cells=6)
    code = ingest.main([
        "--h5ad", str(path), "--dataset-name", "d", "--species-id", "1",
        "--annotation", "nn_label_plain", "--dry-run",
    ])
    assert code == 0
    assert "2 cell types" in capsys.readouterr().out


def test_a_coordinate_at_the_storable_limit_is_accepted_and_past_it_is_not(
    ingest, tmp_path
):
    """The bar is the largest value the explorer's own query can return, so it
    is the limit itself that matters, not an order of magnitude either side."""
    largest_real = 3.4028235e38          # the literal limit, not the constant
    for value, refused in ((largest_real, False),
                           (largest_real * 1.000001, True)):
        coords = np.array([[float(i), 0.0] for i in range(42)])
        coords[3, 1] = value
        path = write_h5ad(tmp_path / f"lim{refused}.h5ad", n_cells=42,
                          labels=["A", "B", "C"] * 14, coords=coords)
        if refused:
            with pytest.raises(ingest.IngestError, match="too large to store"):
                ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)
        else:
            ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_coordinates_that_are_not_numbers_are_refused(ingest, tmp_path):
    """A text obsm survives a round-trip through the file and used to reach
    numpy as an uncaught ValueError."""
    coords = np.array([["a", "b"]] * 120, dtype=object)
    path = write_h5ad(tmp_path / "text.h5ad", n_cells=120,
                      labels=["A", "B", "C", "D"] * 30, coords=coords)
    with pytest.raises(ingest.IngestError, match="does not read as numbers"):
        ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


# --------------------------------------------------------------------------- #
# Barcodes have to name one cell
# --------------------------------------------------------------------------- #


def test_duplicate_barcodes_are_refused(ingest, tmp_path):
    """anndata.concat leaves 10x barcodes repeated across samples unless given
    index_unique, and warns only at concat time. The barcode is the only
    identifier a cell carries, and the documented recovery path when
    coordinates and labels ever arrive separately is a join on it."""
    path = write_h5ad(tmp_path / "dupes.h5ad", n_cells=6,
                      barcodes=["A", "B", "C", "A", "B", "F"])
    with pytest.raises(ingest.IngestError, match="duplicates"):
        ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_the_refusal_counts_the_duplicates(ingest, tmp_path):
    """Naming how many, so an operator can tell one bad concat from a file that
    is mostly fine."""
    path = write_h5ad(tmp_path / "dupes2.h5ad", n_cells=6,
                      barcodes=["A", "A", "A", "D", "E", "F"])
    with pytest.raises(ingest.IngestError, match=r"2 of 6 barcodes are duplicates"):
        ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


@pytest.mark.parametrize("bad", ["", "   ", "nan", "NaN"])
def test_a_cell_with_no_barcode_is_refused(ingest, tmp_path, bad):
    """Blank or the string 'nan' -- what an upstream astype(str) leaves behind.
    Same discipline the cell type and sample columns already hold to."""
    path = write_h5ad(tmp_path / f"blank{abs(hash(bad))}.h5ad", n_cells=6,
                      barcodes=["A", "B", "C", "D", "E", bad])
    with pytest.raises(ingest.IngestError, match="no barcode"):
        ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_distinct_barcodes_load(ingest, tmp_path):
    """The accept case, so the check cannot be satisfied by refusing everything."""
    path = write_h5ad(tmp_path / "fine.h5ad", n_cells=6)
    cells = ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)
    assert len(set(cells["barcodes"])) == 6


# --------------------------------------------------------------------------- #
# The duplicate-point guard, at the severity that actually matters
# --------------------------------------------------------------------------- #


def piled_coords(n_cells: int, piled: int, n_types: int = 4):
    """Ordinary spread-out coordinates with exactly `piled` cells moved onto
    one shared point. That is the partial collision coarse rounding produces, as opposed to
    a wholly unfilled array, and it has to be caught by the duplicate guard
    alone rather than by anything downstream.

    Labels cycle A,B,C,D, so cell i belongs to blob i % n_types.
    """
    coords = np.empty((n_cells, 2), dtype=float)
    for i in range(n_cells):
        rng = np.random.default_rng(i)
        coords[i] = rng.normal(0, 0.01, 2) + np.array([(i % n_types) * 1000.0, 0.0])
    coords[:piled] = [7.0, 7.0]
    return coords


def test_the_smallest_possible_pile_is_refused(ingest, tmp_path):
    """Two cells on one point among 120. Both existing tests pile up 120 cells,
    so every threshold below 120 passed them -- the guard could be narrowed to
    'refuse only at 120+' with the suite still green. This pins the floor."""
    path = write_h5ad(tmp_path / "pair.h5ad", n_cells=120,
                      labels=["A", "B", "C", "D"] * 30,
                      coords=piled_coords(120, 2))
    with pytest.raises(ingest.IngestError, match="2 of 120 cells on a single point"):
        ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_a_small_pile_in_a_large_file_is_refused(ingest, tmp_path):
    """Five cells on one point among 2,000: under the share, over the floor.
    Pins MAX_DUPLICATE_POINT_SHARE itself -- at 0.09 rather than 0.001 this
    would load."""
    path = write_h5ad(tmp_path / "small_pile.h5ad", n_cells=2004,
                      labels=["A", "B", "C", "D"] * 501,
                      samples=["Col-0"] * 2004,
                      coords=piled_coords(2004, 5))
    with pytest.raises(ingest.IngestError, match="5 of 2004 cells on a single point"):
        ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_the_share_is_what_decides_on_a_large_file(ingest, tmp_path):
    """The boundary from the accepting side: 2,004 cells allow two on a point
    (2004 * 0.001 = 2.004, and the test is strictly greater), so this must load.
    Without it the two refusals above are satisfied by refusing everything."""
    path = write_h5ad(tmp_path / "at_bound.h5ad", n_cells=2004,
                      labels=["A", "B", "C", "D"] * 501,
                      samples=["Col-0"] * 2004,
                      coords=piled_coords(2004, 2))
    cells = ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)
    assert cells["n_cells"] == 2004


def test_the_duplicate_share_is_where_it_was_measured(ingest):
    """Pinned because it is a measured constant, and the only thing standing
    between an unfilled obsm and a dataset drawn as one dot."""
    assert ingest.MAX_DUPLICATE_POINT_SHARE == 0.001


# --------------------------------------------------------------------------- #
# What main() hands load(), and what it says afterwards
# --------------------------------------------------------------------------- #
#
# load()'s own tests pass `create` and the options themselves, so nothing there
# sees the wiring. These stand between the flags and the writer.


def _signed_in(ingest, monkeypatch):
    api = ingest.ingest_api
    monkeypatch.setattr(api, "resolve_api", lambda server, url, key, **_: ("http://x/api", "k"))
    monkeypatch.setattr(api, "sign_in", lambda url, key, email, password, **_: api.Session(
        lambda: (None, "bloom_writer", "u1"), None, "bloom_writer", "u1"))
    monkeypatch.setenv("BLOOM_PASSWORD", "pw")
    for var in ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_SERVICE_KEY"):
        monkeypatch.delenv(var, raising=False)


def _wired(ingest, monkeypatch, tmp_path, argv_extra, name="wiring", outcome=None):
    """Run main() signed in against a stand-in, with load() replaced by a
    recorder, and return what it saw."""
    seen = {}

    def recorder(writer, ds_name, species_id, cells, checksum, options, create=False):
        seen.update(name=ds_name, options=options, create=create, cells=cells)
        return 7, cells["n_cells"], outcome or ("registered" if create else "resumed")

    _signed_in(ingest, monkeypatch)
    monkeypatch.setattr(ingest, "load", recorder)
    path = write_h5ad(tmp_path / f"{name}.h5ad", n_cells=240,
                      labels=["A", "B", "C", "D"] * 60)
    code = ingest.main([
        "--h5ad", str(path), "--dataset-name", "d", "--species-id", "1",
        "--annotation", "nn_label_plain", "--server", "https://x",
        "--email", "me@salk.edu", *argv_extra,
    ])
    return code, seen


def test_main_hands_load_the_annotation_not_some_other_column(
    ingest, tmp_path, monkeypatch, capsys
):
    """Handing load() the sample column instead of the annotation would record
    the wrong provenance for the differential expression results, silently."""
    code, seen = _wired(ingest, monkeypatch, tmp_path, [])
    capsys.readouterr()
    assert code == 0
    assert seen["options"]["annotation"] == "nn_label_plain"
    assert seen["name"] == "d"


def test_the_create_flag_reaches_the_writer(ingest, tmp_path, monkeypatch, capsys):
    """Both directions: hard-coded True forks a mistyped name, False makes a
    first load impossible."""
    code, seen = _wired(ingest, monkeypatch, tmp_path, ["--create"], "with")
    capsys.readouterr()
    assert code == 0 and seen["create"] is True

    code, seen = _wired(ingest, monkeypatch, tmp_path, [], "without")
    capsys.readouterr()
    assert code == 0 and seen["create"] is False


@pytest.mark.parametrize("outcome,said", [
    ("registered", "registered dataset 7"), ("resumed", "resumed dataset 7"),
    ("already loaded", "already loaded"),
])
def test_the_closing_line_says_which_happened(ingest, tmp_path, monkeypatch, capsys,
                                              outcome, said):
    code, _ = _wired(ingest, monkeypatch, tmp_path, [], outcome.replace(" ", "_"),
                     outcome=outcome)
    assert code == 0 and said in capsys.readouterr().out


def test_the_expression_units_reach_the_writer(ingest, tmp_path, monkeypatch, capsys):
    """The colourbar label every reader of the dataset sees."""
    code, seen = _wired(ingest, monkeypatch, tmp_path,
                        ["--expression-units", "CPM"], "units")
    capsys.readouterr()
    assert code == 0 and seen["options"]["expression_units"] == "CPM"


def test_a_load_needs_no_database_url_or_service_key(ingest, tmp_path, monkeypatch,
                                                     capsys):
    code, _ = _wired(ingest, monkeypatch, tmp_path, [], "nodb")
    capsys.readouterr()
    assert code == 0


def test_writing_needs_an_email(ingest, tmp_path, monkeypatch, capsys):
    _signed_in(ingest, monkeypatch)
    path = write_h5ad(tmp_path / "noemail.h5ad")
    code = ingest.main(["--h5ad", str(path), "--dataset-name", "d", "--species-id",
                        "1", "--annotation", "nn_label_plain", "--server", "https://x"])
    assert code == 1 and "--email" in capsys.readouterr().err


def test_the_wait_is_checked_before_signing_in(ingest, tmp_path, monkeypatch, capsys):
    """A write that may still be finishing on the server is waited out before
    anything is read, so nothing is written twice."""
    _signed_in(ingest, monkeypatch)
    def no_sign_in(*a, **k):
        raise AssertionError("signed in during the wait")
    monkeypatch.setattr(ingest.ingest_api, "sign_in", no_sign_in)
    path = write_h5ad(tmp_path / "wait.h5ad")
    ingest.ingest_api.Marker(ingest.ingest_api.marker_path(path, "d")).record("insert cells")
    code = ingest.main(["--h5ad", str(path), "--dataset-name", "d", "--species-id",
                        "1", "--annotation", "nn_label_plain", "--server", "https://x",
                        "--email", "me@salk.edu"])
    assert code == 1 and "seconds" in capsys.readouterr().err


def test_a_refusal_does_not_replay_the_file_at_the_terminal(ingest, tmp_path):
    """Barcodes come out of the file, and the refusal is the only thing the
    operator sees. Printed raw, a crafted barcode can clear the screen and paint
    a success line over the failure."""
    hostile = "\x1b[2J\x1b[Hloaded 8683 cells into dataset 3"
    path = write_h5ad(tmp_path / "hostile.h5ad", n_cells=6,
                      barcodes=[hostile, hostile, "C", "D", "E", "F"])
    with pytest.raises(ingest.IngestError) as exc:
        ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)
    message = str(exc.value)
    assert "\x1b" not in message, "escape characters must not reach the terminal"
    assert "\\x1b" in message, "the barcode is still shown, escaped"


@pytest.mark.parametrize("sentinel", ["nan", "None", "NA", "<NA>", "null"])
def test_a_cell_type_that_reads_as_a_missing_value_is_refused(
    ingest, tmp_path, sentinel
):
    """`astype(str)` on a column with missing annotations turns them into these.
    Stored as-is, each becomes a cell type in the legend with no DE rows behind
    it, and a biologist reads unannotated cells as a real population."""
    path = tmp_path / f"sentinel_{sentinel.strip('<>')}.h5ad"
    write_h5ad(path, n_cells=6)
    a = anndata.read_h5ad(path)
    a.obs["nn_label_plain"] = ["Phellem", sentinel, "Phellem",
                               "Cortex", "Cortex", "Cortex"]
    a.write_h5ad(path)
    with pytest.raises(ingest.IngestError, match="missing value"):
        ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_a_real_cell_type_that_merely_looks_odd_still_loads(ingest, tmp_path):
    """The accept case, so the rule above cannot be satisfied by refusing
    anything unusual. 'Nanodomain' contains 'nan'; it is a cell type."""
    path = tmp_path / "nanodomain.h5ad"
    write_h5ad(path, n_cells=6)
    a = anndata.read_h5ad(path)
    a.obs["nn_label_plain"] = ["Nanodomain"] * 3 + ["Cortex"] * 3
    a.write_h5ad(path)
    cells = ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)
    assert cells["levels"] == ["Cortex", "Nanodomain"]
