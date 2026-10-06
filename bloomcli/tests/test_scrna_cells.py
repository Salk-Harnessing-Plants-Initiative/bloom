"""Reading a dataset's cells out of an h5ad, and every refusal that comes before a write.

A half-loaded dataset looks to the explorer exactly like a complete one, so these run against
real `.h5ad` files: a change that stops refusing is caught here.
"""

from __future__ import annotations

from pathlib import Path

import anndata
import numpy as np
import pandas as pd
import pytest

from bloomctl.scrna import _cells, _format
from bloomctl.scrna._writer import LoadError


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
    extra_obs: dict | None = None,
) -> Path:
    """A miniature dataset shaped like the real one."""
    obs = pd.DataFrame(
        {
            annotation: labels or ["Phellem", "Cortex"] * (n_cells // 2),
            sample_column: samples or ["Col-0", "pFACT", "pHORST"] * (n_cells // 3),
            **(extra_obs or {}),
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


def test_reads_a_well_formed_file(tmp_path):
    cells = _cells.read_cells(
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


def test_levels_are_sorted_so_ordinals_are_stable(tmp_path):
    """The same file must produce the same catalogue every time, or a cell type
    changes colour between loads."""
    path = write_h5ad(
        tmp_path / "order.h5ad", n_cells=6,
        labels=["Xylem", "Cortex", "Phellem", "Cortex", "Xylem", "Phellem"],
    )
    first = _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", None)
    second = _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", None)
    assert first["levels"] == second["levels"] == ["Cortex", "Phellem", "Xylem"]


def test_expected_cell_count_passes_when_it_matches(tmp_path):
    _cells.read_cells(
        write_h5ad(tmp_path / "count.h5ad"), "nn_label_plain", "sample", "X_umap", 6
    )


# --------------------------------------------------------------------------- #
# What it refuses, and why
# --------------------------------------------------------------------------- #


def test_missing_coordinates_are_refused(tmp_path):
    """The explorer plots stored coordinates and never computes them, so a file
    without them cannot be shown at all."""
    path = write_h5ad(tmp_path / "nocoords.h5ad", umap_key="")
    with pytest.raises(LoadError, match="X_umap"):
        _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_one_dimensional_coordinates_are_refused(tmp_path):
    path = write_h5ad(tmp_path / "onedim.h5ad", umap_dims=1)
    with pytest.raises(LoadError, match="needs exactly two columns"):
        _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_a_high_dimensional_embedding_is_refused(tmp_path):
    """A 50-column X_pca must not load as a UMAP -- it is the obvious
    workaround when the real coordinates are missing, and the resulting plot is
    indistinguishable from a real one."""
    path = write_h5ad(tmp_path / "pca.h5ad", umap_key="X_pca", umap_dims=50)
    with pytest.raises(LoadError, match="needs exactly two columns"):
        _cells.read_cells(path, "nn_label_plain", "sample", "X_pca", None)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")],
                         ids=["nan", "inf", "-inf"])
def test_non_finite_coordinates_are_refused(tmp_path, bad):
    path = tmp_path / f"nonfinite{bad}.h5ad"
    write_h5ad(path)
    a = anndata.read_h5ad(path)
    coords = np.asarray(a.obsm["X_umap"]).copy()
    coords[1, 0] = bad
    a.obsm["X_umap"] = coords
    a.write_h5ad(path)
    with pytest.raises(LoadError, match="finite"):
        _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_a_missing_label_is_refused_not_stored_as_nan(tmp_path):
    """str(NaN) is "nan", which would become a real cell type in the legend and
    merge with any genuine level of that spelling."""
    path = tmp_path / "nanlabel.h5ad"
    write_h5ad(path)
    a = anndata.read_h5ad(path)
    a.obs["nn_label_plain"] = pd.Categorical(
        ["Phellem", None, "Phellem", "Cortex", "Cortex", "Cortex"]
    )
    a.write_h5ad(path)
    with pytest.raises(LoadError, match="missing value"):
        _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_a_blank_label_is_refused(tmp_path):
    path = tmp_path / "blank.h5ad"
    write_h5ad(path)
    a = anndata.read_h5ad(path)
    a.obs["nn_label_plain"] = ["Phellem", "   ", "Phellem", "Cortex", "Cortex", "Cortex"]
    a.write_h5ad(path)
    with pytest.raises(LoadError, match="blank"):
        _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_a_file_with_no_cells_is_refused(tmp_path):
    """Otherwise it would wipe a loaded dataset and replace it with nothing."""
    path = tmp_path / "empty.h5ad"
    anndata.AnnData(
        X=np.zeros((0, 4), dtype="float32"),
        obs=pd.DataFrame({"nn_label_plain": [], "sample": []}),
        var=pd.DataFrame(index=[f"AT1G0{i}" for i in range(4)]),
    ).write_h5ad(path)
    with pytest.raises(LoadError, match="no cells"):
        _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_missing_annotation_column_is_refused(tmp_path):
    path = write_h5ad(tmp_path / "noann.h5ad")
    with pytest.raises(LoadError, match="saturn_Celltype"):
        _cells.read_cells(path, "saturn_Celltype", "sample", "X_umap", None)


def test_missing_sample_column_is_refused(tmp_path):
    path = write_h5ad(tmp_path / "nosample.h5ad")
    with pytest.raises(LoadError, match="genotype"):
        _cells.read_cells(path, "nn_label_plain", "genotype", "X_umap", None)


def test_unexpected_cell_count_is_refused(tmp_path):
    """Guards against loading a file that is not the one that was reviewed."""
    path = write_h5ad(tmp_path / "shortcount.h5ad", n_cells=6)
    with pytest.raises(LoadError, match="expected 8683"):
        _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", 8683)


def test_as_many_cell_types_as_there_are_colours_is_accepted(tmp_path):
    n = len(_cells.PALETTE)
    labels = [f"type{i}" for i in range(n)] * 21
    path = write_h5ad(tmp_path / "exactly.h5ad", n_cells=n * 21, labels=labels)
    cells = _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", None)
    assert len(cells["levels"]) == n


def test_one_more_cell_type_than_colours_is_refused(tmp_path):
    """Wrapping the palette would draw two cell types identically. One column of
    the real file has exactly 24 levels, so this is one flag away."""
    n = len(_cells.PALETTE) + 1
    labels = [f"type{i}" for i in range(n)] * 21
    path = write_h5ad(tmp_path / "onemore.h5ad", n_cells=n * 21, labels=labels)
    with pytest.raises(LoadError, match="drawn identically"):
        _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_the_palette_has_a_distinct_colour_for_every_cell_type():
    """23 cell types in the target dataset, so 23 distinct colours -- a repeat
    renders two different cell types identically in the plot and the legend."""
    assert len(_cells.PALETTE) == 23
    assert len(set(_cells.PALETTE)) == len(_cells.PALETTE)
    assert all(c.startswith("#") and len(c) == 7 for c in _cells.PALETTE)


def test_unfilled_coordinates_are_refused(tmp_path):
    """An obsm allocated and never filled is finite, 2-D and the right length,
    so every other check here passes it and the plot is a single dot."""
    labels = ["A", "B", "C", "D"] * 30
    path = write_h5ad(tmp_path / "zeros.h5ad", n_cells=120, labels=labels,
                      coords=np.zeros((120, 2)))
    with pytest.raises(LoadError, match="unfilled"):
        _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_partly_unfilled_coordinates_are_refused(tmp_path):
    """The realistic version: most of the array written, the tail left as
    zeros -- so the count of cells on one point is what has to catch it."""
    coords = np.vstack([np.array([[float(i), float(i)] for i in range(1080)]),
                        np.zeros((120, 2))])
    path = write_h5ad(tmp_path / "tail.h5ad", n_cells=1200,
                      labels=["A", "B", "C", "D"] * 300, coords=coords)
    with pytest.raises(LoadError, match="unfilled"):
        _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_coordinates_too_large_to_store_are_refused(tmp_path):
    """Finite, but larger than the REAL column the explorer reads."""
    labels = ["A", "B", "C", "D"] * 30
    coords = np.array([[float(i), 0.0] for i in range(120)], dtype=float)
    coords[7, 0] = 1e300
    path = write_h5ad(tmp_path / "huge.h5ad", n_cells=120, labels=labels,
                      coords=coords)
    with pytest.raises(LoadError, match="too large to store"):
        _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_more_cells_than_expected_is_refused(tmp_path):
    """Both directions, so narrowing the check to one of them fails here."""
    path = write_h5ad(tmp_path / "more.h5ad", n_cells=6)
    with pytest.raises(LoadError, match="expected 3 cells"):
        _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", 3)


def test_a_non_finite_value_in_either_column_is_refused(tmp_path):
    """The y column too, so the check cannot narrow to x and still pass."""
    for column in (0, 1):
        coords = np.array([[float(i), float(i) + 0.5] for i in range(120)])
        coords[5, column] = np.nan
        path = write_h5ad(tmp_path / f"nan{column}.h5ad", n_cells=120,
                          labels=["A", "B", "C", "D"] * 30, coords=coords)
        with pytest.raises(LoadError, match="not finite"):
            _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_three_dimensional_coordinates_are_refused(tmp_path):
    """A (n, 2, k) array has shape[1] == 2, so only the dimension count sees it."""
    coords = np.zeros((120, 2, 3))
    path = write_h5ad(tmp_path / "cube.h5ad", n_cells=120,
                      labels=["A", "B", "C", "D"] * 30, coords=coords)
    with pytest.raises(LoadError, match=r"shape \(120, 2, 3\)"):
        _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_integer_coordinates_are_read_not_crashed_on(tmp_path):
    """An integer obsm used to raise OverflowError out of numpy rather than
    being read or refused."""
    coords = np.array([[(i % 4) * 1000 + i, 0] for i in range(120)], dtype="int64")
    path = write_h5ad(tmp_path / "ints.h5ad", n_cells=120,
                      labels=["A", "B", "C", "D"] * 30, coords=coords)
    cells = _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", None)
    assert cells["x"][:2] == pytest.approx([0.0, 1001.0])
def test_a_coordinate_at_the_storable_limit_is_accepted_and_past_it_is_not(
    tmp_path
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
            with pytest.raises(LoadError, match="too large to store"):
                _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", None)
        else:
            _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_coordinates_that_are_not_numbers_are_refused(tmp_path):
    """A text obsm survives a round-trip through the file and used to reach
    numpy as an uncaught ValueError."""
    coords = np.array([["a", "b"]] * 120, dtype=object)
    path = write_h5ad(tmp_path / "text.h5ad", n_cells=120,
                      labels=["A", "B", "C", "D"] * 30, coords=coords)
    with pytest.raises(LoadError, match="does not read as numbers"):
        _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


# --------------------------------------------------------------------------- #
# Barcodes have to name one cell
# --------------------------------------------------------------------------- #


def test_duplicate_barcodes_are_refused(tmp_path):
    """anndata.concat leaves 10x barcodes repeated across samples unless given
    index_unique, and warns only at concat time. The barcode is the only
    identifier a cell carries, and the documented recovery path when
    coordinates and labels ever arrive separately is a join on it."""
    path = write_h5ad(tmp_path / "dupes.h5ad", n_cells=6,
                      barcodes=["A", "B", "C", "A", "B", "F"])
    with pytest.raises(LoadError, match="duplicates"):
        _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_the_refusal_counts_the_duplicates(tmp_path):
    """Naming how many, so an operator can tell one bad concat from a file that
    is mostly fine."""
    path = write_h5ad(tmp_path / "dupes2.h5ad", n_cells=6,
                      barcodes=["A", "A", "A", "D", "E", "F"])
    with pytest.raises(LoadError, match=r"2 of 6 barcodes are duplicates"):
        _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


@pytest.mark.parametrize("bad", ["", "   ", "nan", "NaN"])
def test_a_cell_with_no_barcode_is_refused(tmp_path, bad):
    """Blank or the string 'nan' -- what an upstream astype(str) leaves behind.
    Same discipline the cell type and sample columns already hold to."""
    path = write_h5ad(tmp_path / f"blank{abs(hash(bad))}.h5ad", n_cells=6,
                      barcodes=["A", "B", "C", "D", "E", bad])
    with pytest.raises(LoadError, match="no barcode"):
        _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_distinct_barcodes_load(tmp_path):
    """The accept case, so the check cannot be satisfied by refusing everything."""
    path = write_h5ad(tmp_path / "fine.h5ad", n_cells=6)
    cells = _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", None)
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


def test_the_smallest_possible_pile_is_refused(tmp_path):
    """Two cells on one point among 120. Both existing tests pile up 120 cells,
    so every threshold below 120 passed them -- the guard could be narrowed to
    'refuse only at 120+' with the suite still green. This pins the floor."""
    path = write_h5ad(tmp_path / "pair.h5ad", n_cells=120,
                      labels=["A", "B", "C", "D"] * 30,
                      coords=piled_coords(120, 2))
    with pytest.raises(LoadError, match="2 of 120 cells on a single point"):
        _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_a_small_pile_in_a_large_file_is_refused(tmp_path):
    """Five cells on one point among 2,000: under the share, over the floor.
    Pins MAX_DUPLICATE_POINT_SHARE itself -- at 0.09 rather than 0.001 this
    would load."""
    path = write_h5ad(tmp_path / "small_pile.h5ad", n_cells=2004,
                      labels=["A", "B", "C", "D"] * 501,
                      samples=["Col-0"] * 2004,
                      coords=piled_coords(2004, 5))
    with pytest.raises(LoadError, match="5 of 2004 cells on a single point"):
        _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_the_share_is_what_decides_on_a_large_file(tmp_path):
    """The boundary from the accepting side: 2,004 cells allow two on a point
    (2004 * 0.001 = 2.004, and the test is strictly greater), so this must load.
    Without it the two refusals above are satisfied by refusing everything."""
    path = write_h5ad(tmp_path / "at_bound.h5ad", n_cells=2004,
                      labels=["A", "B", "C", "D"] * 501,
                      samples=["Col-0"] * 2004,
                      coords=piled_coords(2004, 2))
    cells = _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", None)
    assert cells["n_cells"] == 2004


def test_the_duplicate_share_is_where_it_was_measured():
    """Pinned because it is a measured constant, and the only thing standing
    between an unfilled obsm and a dataset drawn as one dot."""
    assert _format.MAX_DUPLICATE_POINT_SHARE == 0.001


# --------------------------------------------------------------------------- #
# What main() hands load(), and what it says afterwards
# --------------------------------------------------------------------------- #
#
# load()'s own tests pass `create` and the options themselves, so nothing there
# sees the wiring. These stand between the flags and the writer.


@pytest.mark.parametrize("sentinel", ["nan", "None", "NA", "<NA>", "null"])
def test_a_cell_type_that_reads_as_a_missing_value_is_refused(
    tmp_path, sentinel
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
    with pytest.raises(LoadError, match="missing value"):
        _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", None)


def test_a_real_cell_type_that_merely_looks_odd_still_loads(tmp_path):
    """The accept case, so the rule above cannot be satisfied by refusing
    anything unusual. 'Nanodomain' contains 'nan'; it is a cell type."""
    path = tmp_path / "nanodomain.h5ad"
    write_h5ad(path, n_cells=6)
    a = anndata.read_h5ad(path)
    a.obs["nn_label_plain"] = ["Nanodomain"] * 3 + ["Cortex"] * 3
    a.write_h5ad(path)
    cells = _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", None)
    assert cells["levels"] == ["Cortex", "Nanodomain"]


# --------------------------------------------------------------------------- #
# Labels the map can filter on, and genotypes
# --------------------------------------------------------------------------- #

LABELLED_OBS = {
    "transgene_pos": [False, True, False, True, False, False],
    "saturn_timezone": ["Meristem", "Elongation", "Meristem", "Maturation",
                        "Elongation", "Meristem"],
}


def test_label_columns_are_read_per_cell_as_text(tmp_path):
    path = write_h5ad(tmp_path / "labels.h5ad", extra_obs=LABELLED_OBS)
    cells = _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", None,
                              facet_columns=("transgene_pos", "saturn_timezone"))
    assert cells["facets"][0] == {"transgene_pos": "False", "saturn_timezone": "Meristem"}
    assert cells["facets"][1] == {"transgene_pos": "True", "saturn_timezone": "Elongation"}
    assert len(cells["facets"]) == 6


def test_no_label_columns_means_no_labels(tmp_path):
    cells = _cells.read_cells(write_h5ad(tmp_path / "plain.h5ad"), "nn_label_plain",
                              "sample", "X_umap", None)
    assert cells["facets"] is None and cells["genotypes"] is None


def test_a_label_column_the_file_lacks_is_refused(tmp_path):
    path = write_h5ad(tmp_path / "nolabel.h5ad")
    with pytest.raises(LoadError, match="no obs\\['transgene_pos'\\]"):
        _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", None,
                          facet_columns=("transgene_pos",))


def test_a_label_column_with_too_many_values_is_refused(tmp_path):
    """A row of toggles holds a handful of values; hundreds means a measurement."""
    path = write_h5ad(tmp_path / "many.h5ad", n_cells=14,
                      labels=["Phellem", "Cortex"] * 7,
                      samples=["Col-0", "pFACT"] * 7,
                      extra_obs={"score": [f"v{i}" for i in range(14)]})
    with pytest.raises(LoadError, match="14 values, more than the 12"):
        _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", None,
                          facet_columns=("score",))


def test_a_blank_facet_value_is_refused(tmp_path):
    path = write_h5ad(tmp_path / "blanklabel.h5ad",
                      extra_obs={"transgene_pos": ["True", "", "False", "True", "False", "True"]})
    with pytest.raises(LoadError, match="blank"):
        _cells.read_cells(path, "nn_label_plain", "sample", "X_umap", None,
                          facet_columns=("transgene_pos",))


def test_genotypes_are_read_per_cell(tmp_path):
    cells = _cells.read_cells(write_h5ad(tmp_path / "geno.h5ad"), "nn_label_plain",
                              "sample", "X_umap", None, genotype_column="sample")
    assert cells["genotypes"] == ["Col-0", "pFACT", "pHORST"] * 2


def test_genotype_rows_name_the_control_and_each_construct():
    rows = _cells.genotype_rows(["pHORST", "Col-0", "pFACT"], "Col-0",
                                {"pFACT": "pFACT:MYB41", "pHORST": "pHORST:MYB41"})
    assert rows == [
        {"name": "Col-0", "is_control": True, "construct": None},
        {"name": "pFACT", "is_control": False, "construct": "pFACT:MYB41"},
        {"name": "pHORST", "is_control": False, "construct": "pHORST:MYB41"},
    ]


@pytest.mark.parametrize("control,constructs,named", [
    (None, {}, "--control"),
    ("WT", {}, "WT"),
    ("Col-0", {"pFOO": "x"}, "pFOO"),
])
def test_genotype_rows_refuse_what_the_file_does_not_hold(control, constructs, named):
    with pytest.raises(LoadError, match=named):
        _cells.genotype_rows(["Col-0", "pFACT"], control, constructs)


@pytest.mark.parametrize("bad", ["pFACT", "=x", "pFACT="])
def test_a_malformed_construct_is_refused(bad):
    with pytest.raises(LoadError, match="GENOTYPE=NAME"):
        _cells.parse_constructs([bad])


