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
    n_types: int = 2,
    samples: list[str] | None = None,
    coords: "np.ndarray | None" = None,
    barcodes: list[str] | None = None,
) -> Path:
    """A miniature dataset shaped like the real one."""
    obs = pd.DataFrame(
        {
            annotation: labels or [
                f"Type{i % n_types}" for i in range(n_cells)
            ] if n_types != 2 else (labels or ["Phellem", "Cortex"] * (n_cells // 2)),
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


# --------------------------------------------------------------------------- #
# The alignment statistic itself
#
# `neighbour_purity` is the only real algorithm here, and it had no direct test:
# breaking its self-exclusion or its chunk boundary left every other test green
# while inflating a shuffled embedding to two thirds of the safety margin. These
# assert exact values, so a weakened version cannot pass.
# --------------------------------------------------------------------------- #


def _groups(n_groups: int, per_group: int):
    """Cells in tight, far-apart groups: one label per group, so every cell's
    nearest neighbours are its own group and the answer is arithmetic."""
    coords, labels = [], []
    for g in range(n_groups):
        for i in range(per_group):
            coords.append([g * 1000.0 + i * 0.001, 0.0])
            labels.append(f"type{g}")
    return np.array(coords), labels


def test_a_cell_is_not_its_own_neighbour(ingest):
    """Groups of 15 with k=15: each cell has 14 same-label neighbours and must
    reach outside its group for the 15th. Counting itself would give 1.0."""
    coords, labels = _groups(4, 15)
    assert ingest.neighbour_purity(coords, labels) == pytest.approx(14 / 15)


def test_groups_larger_than_k_are_perfectly_pure(ingest):
    coords, labels = _groups(4, 16)
    assert ingest.neighbour_purity(coords, labels) == pytest.approx(1.0)


def test_purity_is_the_same_across_the_chunk_boundary(ingest):
    """600 cells in groups of 10 spans the 512-row block, so a per-chunk index
    mistake or a loop that stops after one block shows up here and nowhere else.
    Every cell has 9 same-label neighbours out of 15."""
    coords, labels = _groups(60, 10)
    assert ingest.neighbour_purity(coords, labels) == pytest.approx(9 / 15)


def test_a_shuffle_scores_at_chance(ingest):
    coords, labels = _groups(20, 30)
    rng = np.random.default_rng(0)
    shuffled = coords[rng.permutation(len(coords))]
    scored = ingest.alignment(shuffled, labels)
    assert scored is not None
    assert scored[1] < 0.02, scored


def test_the_excess_is_scaled_by_the_room_above_chance(ingest):
    """Four groups of 16 put chance at 0.25 and purity at 1.0. Scaled, that is
    1.0. Unscaled it would be 0.75 and as a ratio 3.0 -- so this pins the
    formula, not just its sign."""
    coords, labels = _groups(4, 16)
    purity, excess = ingest.alignment(coords, labels)
    assert purity == pytest.approx(1.0)
    assert excess == pytest.approx(1.0)


def test_the_bar_is_where_it_was_measured(ingest):
    """Calibrated on the first dataset: the weakest legitimate coordinates score
    0.21 and a shuffle of them 0.00, so the bar sits half way down. Loosening it
    is the one change here that no behavioural test would notice."""
    assert ingest.MIN_ALIGNMENT_EXCESS == 0.10


def test_too_few_cells_or_cell_types_to_judge(ingest):
    coords, labels = _groups(4, 5)               # 20 cells
    assert ingest.alignment(coords, labels) is None
    coords, labels = _groups(1, 90)              # one cell type
    assert ingest.alignment(coords, labels) is None


def test_the_gate_boundaries_are_exact(ingest):
    """One cell or one cell type either side of the gate."""
    coords, labels = _groups(5, 10)          # 50 cells, 5 types
    assert ingest.alignment(coords, labels) is not None
    assert ingest.alignment(coords[:49], labels[:49]) is None
    coords, labels = _groups(2, 30)          # 2 cell types is enough to judge
    assert ingest.alignment(coords, labels) is not None
    coords, labels = _groups(1, 60)          # 1 is not
    assert ingest.alignment(coords, labels) is None


# --------------------------------------------------------------------------- #
# Coordinates that are present but not real
# --------------------------------------------------------------------------- #


def test_unfilled_coordinates_are_refused(ingest, tmp_path):
    """An obsm allocated and never filled is finite, 2-D and the right length.
    The neighbour check does not merely miss it -- with every distance tied it
    scores it as well aligned -- so it has to be refused on its own."""
    labels = ["A", "B", "C", "D"] * 30
    path = write_h5ad(tmp_path / "zeros.h5ad", n_cells=120, labels=labels,
                      coords=np.zeros((120, 2)))
    with pytest.raises(ingest.IngestError, match="unfilled"):
        ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_partly_unfilled_coordinates_are_refused(ingest, tmp_path):
    """The realistic version: most of the slice lined up, the tail left as
    zeros. This passes the neighbour check on both of the real file's
    annotations."""
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


def test_one_sample_misaligned_is_refused(ingest, tmp_path):
    """The failure a single average over the whole file cannot see: two samples
    right, one shuffled within itself. The global score stays above the bar."""
    n_per, types = 60, ["A", "B", "C", "D"]
    labels, samples, coords = [], [], []
    for si, s in enumerate(("Col-0", "pFACT", "pHORST")):
        for i in range(n_per):
            labels.append(types[i % 4])
            samples.append(s)
            # every cell its own point, but far closer to its own cell type
            # than to any other, as in a real embedding
            coords.append([(i % 4) * 1000.0 + (si * n_per + i) * 0.01, 0.0])
    coords = np.array(coords)
    bad = np.arange(n_per, 2 * n_per)
    rng = np.random.default_rng(0)
    coords[bad] = coords[rng.permutation(bad)]

    whole = ingest.alignment(coords, labels)
    assert whole[1] >= ingest.MIN_ALIGNMENT_EXCESS, (
        "the point of this test is that the whole-file score still passes"
    )
    path = write_h5ad(tmp_path / "onesample.h5ad", n_cells=len(labels),
                      labels=labels, samples=samples, coords=coords)
    with pytest.raises(ingest.IngestError, match=r"obs\['sample'\] == 'pFACT'"):
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


def test_a_misaligned_group_that_is_not_a_sample_is_refused(ingest, tmp_path):
    """The real file is two source datasets joined, and that split cuts across
    the samples: misorder one source and every sample still looks fine. Scoring
    only the sample column misses it, which is why every grouping is scored."""
    types = ["A", "B", "C", "D"]
    labels, samples, source, coords = [], [], [], []
    for i in range(360):
        labels.append(types[i % 4])
        samples.append(["Col-0", "pFACT", "pHORST"][i % 3])
        source.append("nuclei" if (i // 4) % 2 else "shahan")
        coords.append([(i % 4) * 1000.0 + i * 0.01, 0.0])
    coords = np.array(coords)
    bad = np.flatnonzero(np.asarray(source) == "shahan")
    rng = np.random.default_rng(0)
    coords[bad] = coords[rng.permutation(bad)]

    for sample in ("Col-0", "pFACT", "pHORST"):
        rows = np.flatnonzero(np.asarray(samples) == sample)
        scored = ingest.alignment(coords[rows], [labels[i] for i in rows])
        assert scored is None or scored[1] >= ingest.MIN_ALIGNMENT_EXCESS, (
            f"{sample} must still look fine, or this tests nothing"
        )

    path = write_h5ad(tmp_path / "twosource.h5ad", n_cells=len(labels),
                      labels=labels, samples=samples, coords=coords)
    import anndata
    adata = anndata.read_h5ad(path)
    adata.obs["nn_source"] = source
    adata.write_h5ad(path)

    with pytest.raises(ingest.IngestError, match=r"obs\['nn_source'\] == 'shahan'"):
        ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None,
                          ("nn_source",))

    # and unnamed, it is not scored -- the operator says which columns are
    # provenance, because the script cannot tell them from biology
    ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_an_unknown_group_column_is_refused(ingest, tmp_path):
    path = write_h5ad(tmp_path / "nogroup.h5ad", n_cells=120,
                      labels=["A", "B", "C", "D"] * 30)
    with pytest.raises(ingest.IngestError, match="no obs\\['nope'\\] to group by"):
        ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None,
                          ("nope",))


def test_the_sample_column_is_grouped_without_being_named(ingest, tmp_path):
    """It is provenance by definition, so it is never left to be remembered."""
    import anndata
    adata = anndata.read_h5ad(
        write_h5ad(tmp_path / "s.h5ad", n_cells=120, labels=["A", "B", "C", "D"] * 30)
    )
    assert ingest._grouping_columns(adata, "nn_label_plain", "sample", ()) == ["sample"]
    assert ingest._grouping_columns(adata, "nn_label_plain", "batch", ("sample",)) == [
        "batch", "sample"
    ]


def test_the_annotation_is_never_grouped_by(ingest, tmp_path):
    """Grouping by it would score cells against their own label, which measures
    nothing -- even if the operator names it."""
    import anndata
    adata = anndata.read_h5ad(
        write_h5ad(tmp_path / "a.h5ad", n_cells=120, labels=["A", "B", "C", "D"] * 30)
    )
    assert ingest._grouping_columns(
        adata, "nn_label_plain", "sample", ("nn_label_plain",)
    ) == ["sample"]


def test_every_named_group_column_is_scored_not_just_the_first(ingest, tmp_path):
    """The damage is in the column that sorts last, so stopping early passes."""
    types = ["A", "B", "C", "D"]
    labels, samples, first, second, coords = [], [], [], [], []
    for i in range(360):
        labels.append(types[i % 4])
        samples.append(["Col-0", "pFACT", "pHORST"][i % 3])
        first.append("aaa_fine")
        second.append("zzz_bad" if (i // 4) % 2 else "zzz_ok")
        coords.append([(i % 4) * 1000.0 + i * 0.01, 0.0])
    coords = np.array(coords)
    bad = np.flatnonzero(np.asarray(second) == "zzz_bad")
    coords[bad] = coords[np.random.default_rng(0).permutation(bad)]

    path = write_h5ad(tmp_path / "twocols.h5ad", n_cells=len(labels),
                      labels=labels, samples=samples, coords=coords)
    import anndata
    adata = anndata.read_h5ad(path)
    adata.obs["aaa_col"] = first
    adata.obs["zzz_col"] = second
    adata.write_h5ad(path)

    with pytest.raises(ingest.IngestError, match=r"obs\['zzz_col'\] == 'zzz_bad'"):
        ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None,
                          ("aaa_col", "zzz_col"))


def test_a_group_is_scored_against_its_own_labels(ingest, tmp_path):
    """Labels within a group must be paired with that group's own rows. The
    earlier fixtures were periodic, so any mispairing was a no-op."""
    rng = np.random.default_rng(7)
    labels, samples, coords = [], [], []
    order = list(rng.permutation([t for t in ["A", "B", "C", "D"] for _ in range(45)]))
    for i, t in enumerate(order):
        labels.append(t)
        samples.append(["Col-0", "pFACT", "pHORST"][i % 3])
        coords.append([ord(t) * 1000.0 + i * 0.01, 0.0])
    path = write_h5ad(tmp_path / "nonperiodic.h5ad", n_cells=len(labels),
                      labels=labels, samples=samples, coords=np.array(coords))
    cells = ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)
    assert cells["n_cells"] == 180


def test_main_reads_the_annotation_the_operator_named(ingest, tmp_path, capsys):
    """Pins the reader wiring only -- a dry run stops before the database."""
    path = write_h5ad(tmp_path / "wiring.h5ad", n_cells=6)
    code = ingest.main([
        "--h5ad", str(path), "--dataset-name", "d", "--species-id", "1",
        "--annotation", "nn_label_plain", "--dry-run",
    ])
    assert code == 0
    assert "2 cell types" in capsys.readouterr().out


def test_main_hands_load_the_annotation_and_the_group_columns(
    ingest, tmp_path, monkeypatch
):
    """The other half of the wiring, which a dry run cannot reach. Handing
    `load()` the sample column instead of the annotation would record the wrong
    provenance for the differential expression results, silently."""
    import contextlib
    import psycopg

    seen = {}

    def recorder(conn, name, species_id, cells, checksum, units, annotation,
                 create=False):
        seen.update(name=name, annotation=annotation, cells=cells)
        return 1, cells["n_cells"], create

    monkeypatch.setattr(ingest, "load", recorder)
    monkeypatch.setattr(psycopg, "connect", lambda url: contextlib.nullcontext(None))
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@nowhere/none")

    path = write_h5ad(tmp_path / "wiring2.h5ad", n_cells=240,
                      labels=["A", "B", "C", "D"] * 60)
    import anndata
    adata = anndata.read_h5ad(path)
    adata.obs["origin"] = ["one" if i % 2 else "two" for i in range(240)]
    adata.write_h5ad(path)

    assert ingest.main([
        "--h5ad", str(path), "--dataset-name", "d", "--species-id", "1",
        "--annotation", "nn_label_plain", "--group-column", "origin",
    ]) == 0
    assert seen["annotation"] == "nn_label_plain"
    assert seen["name"] == "d"
    assert "origin" in [c for c, _, _ in seen["cells"]["grouped"]], (
        "the named grouping column must reach the reader"
    )


def _uneven(n_per=80):
    """Three samples whose alignment differs, so a report that prints the wrong
    one of them is visible. The second is scrambled a little -- enough to score
    lower, not enough to be refused."""
    types = ["A", "B", "C", "D"]
    labels, samples, coords = [], [], []
    for si, sample in enumerate(("Col-0", "pFACT", "pHORST")):
        for i in range(n_per):
            labels.append(types[i % 4])
            samples.append(sample)
            coords.append([(i % 4) * 1000.0 + (si * n_per + i) * 0.01, 0.0])
    coords = np.array(coords)
    rows = np.arange(n_per, n_per + 12)
    coords[rows] = coords[np.random.default_rng(1).permutation(rows)]
    return labels, samples, coords


def test_the_summary_reports_the_weakest_group_it_actually_scored(ingest, tmp_path):
    """The report is what tells the operator whether their --group-column did
    anything, so its numbers are pinned: the count, the minimum rather than the
    maximum, and the excess rather than the raw agreement."""
    labels, samples, coords = _uneven()
    path = write_h5ad(tmp_path / "report.h5ad", n_cells=len(labels),
                      labels=labels, samples=samples, coords=coords)
    cells = ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)

    scored = {}
    for sample in sorted(set(samples)):
        rows = np.flatnonzero(np.asarray(samples) == sample)
        scored[sample] = ingest.alignment(coords[rows], [labels[i] for i in rows])
    excesses = [e for _, e in scored.values()]
    purities = [p for p, _ in scored.values()]
    assert min(excesses) < max(excesses), "the fixture must not be symmetric"
    assert min(excesses) != pytest.approx(min(purities), abs=1e-3)

    out = ingest.summarise(cells)
    assert "grouped by sample: 3 of 3 groups scored" in out
    assert f"weakest {min(excesses):.3f} of the way" in out


def test_the_summary_says_when_a_grouping_column_judged_nothing(ingest, tmp_path):
    """Both ways a column buys nothing: groups too small, and groups plenty big
    but holding one cell type each -- which is what a column correlated with the
    annotation gives."""
    path = write_h5ad(tmp_path / "nothing.h5ad", n_cells=240,
                      labels=["A", "B", "C", "D"] * 60)
    import anndata
    adata = anndata.read_h5ad(path)
    adata.obs["reading"] = [str(i) for i in range(240)]          # 240 tiny groups
    adata.obs["mirrors"] = list(adata.obs["nn_label_plain"])     # big, one type each
    adata.write_h5ad(path)

    for column, n_groups in (("reading", 240), ("mirrors", 4)):
        cells = ingest.read_cells(path, "nn_label_plain", "sample", "X_umap",
                                  None, (column,))
        out = ingest.summarise(cells)
        assert f"grouped by {column}: 0 of {n_groups} groups scored" in out
        assert "none had both enough cells and enough cell types" in out


def test_only_a_named_group_column_is_blamed_on_biology(ingest, tmp_path):
    """The hint to stop grouping by a column would be wrong for the sample
    column, which is always scored and cannot be opted out of."""
    labels, samples, coords = _uneven()
    rows = np.arange(80, 160)
    coords[rows] = coords[np.random.default_rng(2).permutation(rows)]
    path = write_h5ad(tmp_path / "blame.h5ad", n_cells=len(labels),
                      labels=labels, samples=samples, coords=coords)
    with pytest.raises(ingest.IngestError) as caught:
        ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None)
    assert "obs['sample'] == 'pFACT'" in str(caught.value)
    assert "do not group by it" not in str(caught.value)


def test_a_named_group_column_gets_the_biology_hint(ingest, tmp_path):
    """Naming a biological column refuses a correct file, so the refusal has to
    offer that explanation -- the damage here is in a column that cuts across
    the samples, so the samples themselves pass."""
    types = ["A", "B", "C", "D"]
    labels, samples, zone, coords = [], [], [], []
    for i in range(360):
        labels.append(types[i % 4])
        samples.append(["Col-0", "pFACT", "pHORST"][i % 3])
        zone.append("meristem" if (i // 4) % 2 else "mature")
        coords.append([(i % 4) * 1000.0 + i * 0.01, 0.0])
    coords = np.array(coords)
    bad = np.flatnonzero(np.asarray(zone) == "meristem")
    coords[bad] = coords[np.random.default_rng(0).permutation(bad)]

    path = write_h5ad(tmp_path / "hint.h5ad", n_cells=len(labels),
                      labels=labels, samples=samples, coords=coords)
    import anndata
    adata = anndata.read_h5ad(path)
    adata.obs["zone"] = zone
    adata.write_h5ad(path)

    with pytest.raises(ingest.IngestError) as caught:
        ingest.read_cells(path, "nn_label_plain", "sample", "X_umap", None,
                          ("zone",))
    assert "obs['zone'] == 'meristem'" in str(caught.value)
    assert "do not group by it" in str(caught.value)


def test_the_group_column_flag_collects_every_name(ingest):
    base = ["--h5ad", "x", "--dataset-name", "d", "--species-id", "1",
            "--annotation", "a"]
    assert ingest.parse_args(base).group_column == []
    assert ingest.parse_args(
        base + ["--group-column", "one", "--group-column", "two"]
    ).group_column == ["one", "two"]


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
# The score is measured against what the labels allow, not against a perfect 1.0
# --------------------------------------------------------------------------- #


def separated(n_types: int, per_type: int):
    """Coordinates that are as right as coordinates can be: each cell type a
    tight blob, blobs far apart. Any refusal of these is a false refusal."""
    coords, labels = [], []
    for t in range(n_types):
        rng = np.random.default_rng(t)
        coords.append(rng.normal(0, 0.01, (per_type, 2)) + np.array([t * 1000.0, 0.0]))
        labels += [f"Type{t}"] * per_type
    return np.vstack(coords).astype(float), labels


def test_attainable_purity_is_capped_by_the_smallest_cell_type(ingest):
    """A cell has 15 neighbours and only its own type can fill them, so a type
    of three caps each of its cells at two of fifteen. Pinned by hand: 23 types
    of three is 23*3 cells each scoring at best 2/15."""
    labels = [f"Type{i}" for i in range(23) for _ in range(3)]
    assert ingest.attainable_purity(labels) == pytest.approx(2 / 15)

    # A type larger than the neighbourhood is not capped at all.
    assert ingest.attainable_purity(["A"] * 100 + ["B"] * 100) == pytest.approx(1.0)

    # Mixed: 100 cells of A reach 15/15, 3 of B reach 2/15.
    mixed = ["A"] * 100 + ["B"] * 3
    expected = (100 * 15 + 3 * 2) / (103 * 15)
    assert ingest.attainable_purity(mixed) == pytest.approx(expected)


@pytest.mark.parametrize("n_types, per_type", [(23, 3), (23, 4), (10, 5), (25, 2)])
def test_perfect_coordinates_are_accepted_however_small_the_cell_types(
    ingest, n_types, per_type
):
    """The regression this section exists for. Scored against a perfect 1.0,
    23 types of three tops out at 0.133 against a bar of 0.139 -- so flawless
    coordinates were refused, and no arrangement could have passed."""
    coords, labels = separated(n_types, per_type)
    scored = ingest.alignment(coords, labels)
    assert scored is not None, "these shapes must be judgeable, not skipped"
    purity, excess = scored
    assert excess == pytest.approx(1.0), (
        f"{n_types} types of {per_type} scored {excess:.3f}; perfect coordinates "
        f"must reach the top of the range whatever the type sizes"
    )
    assert excess >= ingest.MIN_ALIGNMENT_EXCESS


@pytest.mark.parametrize("n_types, per_type", [(23, 3), (10, 5)])
def test_shuffled_coordinates_are_still_refused_at_those_sizes(
    ingest, n_types, per_type
):
    """The other half: widening the range must not have made it unfalsifiable."""
    coords, labels = separated(n_types, per_type)
    rng = np.random.default_rng(0)
    rng.shuffle(coords)
    _, excess = ingest.alignment(coords, labels)
    assert excess < ingest.MIN_ALIGNMENT_EXCESS


def test_labels_leaving_no_room_above_chance_are_skipped_not_refused(ingest):
    """One cell per type: nothing can share a neighbourhood, so the ceiling is 0
    and the check has nothing to say. Skipped, as too-few-cells already is --
    refusing would be refusing data the check cannot judge."""
    labels = [f"Type{i}" for i in range(60)]
    coords = np.array([[float(i), float(i)] for i in range(60)])
    assert ingest.alignment(coords, labels) is None


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
    """Properly clustered coordinates -- each cell type its own blob, so the
    alignment check passes -- with exactly `piled` cells moved onto one shared
    point. That is the partial collision coarse rounding produces, as opposed to
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
    """Pinned like the alignment bar, because it is the same kind of constant:
    measured, and the only thing standing between an unfilled array and a load
    that scores better than the real embedding."""
    assert ingest.MAX_DUPLICATE_POINT_SHARE == 0.001
