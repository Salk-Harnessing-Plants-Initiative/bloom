"""bloomctl scrna — the structure check for Bloom's h5ad format."""

from pathlib import Path

import numpy as np
import pytest
from scrna_fixtures import NORMALIZATION, write_h5ad

from bloomctl.scrna import _format as fmt

MYB41 = Path.home() / "Downloads" / "MYB41_load" / "myb41_transgene_load.h5ad"


def _refused(path, match):
    with pytest.raises(fmt.FormatError, match=match):
        fmt.check_structure(path)


# --- files that meet the format ----------------------------------------------


@pytest.mark.parametrize("index_style", ["nullable", "plain"])
@pytest.mark.parametrize("sparse", [True, False])
def test_a_file_that_meets_the_format_is_summarised(tmp_path, index_style, sparse):
    path = write_h5ad(tmp_path / "ok.h5ad", index_style=index_style, sparse=sparse)
    summary = fmt.check_structure(path)
    assert (summary.n_cells, summary.n_genes) == (3, 4)
    assert summary.normalization == NORMALIZATION


def test_fractional_counts_are_accepted(tmp_path):
    fmt.check_structure(write_h5ad(tmp_path / "f.h5ad", counts=np.full((3, 4), 0.25)))


def test_a_file_without_counts_is_accepted(tmp_path):
    fmt.check_structure(
        write_h5ad(tmp_path / "f.h5ad", counts=None,
                   normalization={"transform": "log1p", "scaling": "library_size",
                                  "target_sum": 10000})
    )


def test_gene_ids_of_another_species_are_left_alone(tmp_path):
    ids = ["Zm00001eb000010", "Zm00001eb000020", "Solyc01g005000.3", "GRMZM2G000001"]
    fmt.check_structure(write_h5ad(tmp_path / "maize.h5ad", var_ids=ids))


def test_a_normalization_de_cannot_use_is_still_a_valid_file(tmp_path):
    block = {"transform": "none", "scaling": "other",
             "description": "SCTransform Pearson residuals"}
    summary = fmt.check_structure(write_h5ad(tmp_path / "sct.h5ad", counts=None,
                                             normalization=block))
    assert summary.normalization == block


def test_a_missing_normalization_is_reported_not_refused(tmp_path):
    """Whether it is allowed depends on the dataset record, which the command checks."""
    summary = fmt.check_structure(write_h5ad(tmp_path / "old.h5ad", normalization=None))
    assert summary.normalization is None


@pytest.mark.skipif(not MYB41.exists(), reason="MYB41's load file is not on this machine")
def test_myb41s_load_file_meets_the_format():
    summary = fmt.check_structure(MYB41)
    assert (summary.n_cells, summary.n_genes) == (8683, 27656)
    assert summary.normalization is None  # it predates the block; dataset 14 records it


# --- files that do not -------------------------------------------------------


def test_an_hdf5_file_that_is_not_anndata_is_refused(tmp_path):
    _refused(write_h5ad(tmp_path / "raw.h5ad", anndata=False), "not an AnnData file")


def test_a_file_that_is_not_hdf5_is_refused(tmp_path):
    path = tmp_path / "cells.h5ad"
    path.write_text("cell,gene\n")
    _refused(path, "not an HDF5 file")


def test_a_file_without_x_is_refused(tmp_path):
    _refused(write_h5ad(tmp_path / "f.h5ad", with_x=False), r"no X")


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
@pytest.mark.parametrize("sparse", [True, False])
def test_a_non_finite_expression_value_is_refused(tmp_path, bad, sparse):
    x = np.array([[0.0, 1.5, 0.0, 2.0], [0.7, bad, 0.0, 0.0], [0.0, 0.0, 3.1, 0.2]])
    _refused(write_h5ad(tmp_path / "f.h5ad", x=x, sparse=sparse), "X holds a value that is not finite")


@pytest.mark.parametrize("index_style", ["nullable", "plain"])
def test_repeated_cell_ids_are_refused(tmp_path, index_style):
    path = write_h5ad(tmp_path / "f.h5ad", obs_ids=["AAAC", "AAAG", "AAAC"],
                      index_style=index_style)
    _refused(path, "cell ID 'AAAC' appears more than once")


def test_a_missing_cell_id_is_refused(tmp_path):
    _refused(write_h5ad(tmp_path / "f.h5ad", obs_blanks=[1]), "cell 2 has no ID")


def test_a_blank_cell_id_is_refused(tmp_path):
    _refused(write_h5ad(tmp_path / "f.h5ad", obs_ids=["a", "  ", "c"]), "cell 2 has no ID")


def test_a_gene_listed_twice_is_refused(tmp_path):
    path = write_h5ad(tmp_path / "f.h5ad", var_ids=["g1", "g2", "g1", "g4"])
    _refused(path, "gene ID 'g1' appears more than once")


def test_an_index_shorter_than_x_is_refused(tmp_path):
    _refused(write_h5ad(tmp_path / "f.h5ad", obs_ids=["a", "b"]), "2 cell IDs for 3 rows of X")


def test_a_file_whose_umap_is_not_named_x_umap_is_refused(tmp_path):
    """The loader reads obsm['X_umap'] by name; another two-column array is not it."""
    _refused(write_h5ad(tmp_path / "f.h5ad", obsm={"spatial": (3, 2)}), r"obsm\['X_umap'\]")


def test_an_obsm_array_with_the_wrong_row_count_is_refused(tmp_path):
    _refused(write_h5ad(tmp_path / "f.h5ad", obsm={"X_umap": (2, 2)}), r"obsm\['X_umap'\]")


def test_a_umap_with_more_than_two_columns_is_refused(tmp_path):
    _refused(write_h5ad(tmp_path / "f.h5ad", obsm={"X_umap": (3, 3)}), r"obsm\['X_umap'\]")


def test_a_umap_holding_a_non_finite_coordinate_is_refused(tmp_path):
    coordinates = np.array([[0.0, 1.0], [2.0, np.nan], [4.0, 5.0]], dtype=np.float32)
    _refused(write_h5ad(tmp_path / "f.h5ad", umap=coordinates), "not finite")


def test_a_umap_that_puts_every_cell_on_one_point_is_refused(tmp_path):
    """An embedding that was never filled in; the loader refuses it too."""
    _refused(write_h5ad(tmp_path / "f.h5ad", umap=np.zeros((3, 2), dtype=np.float32)),
             "single point")


# --- malformed files say so, rather than raising ------------------------------


def _break(path, edit):
    import h5py

    with h5py.File(path, "r+") as f:
        edit(f)
    return path


def test_a_matrix_without_its_shape_says_so(tmp_path):
    path = _break(write_h5ad(tmp_path / "f.h5ad"), lambda f: f["X"].attrs.__delitem__("shape"))
    _refused(path, "X")


def test_a_matrix_without_its_values_says_so(tmp_path):
    path = _break(write_h5ad(tmp_path / "f.h5ad"), lambda f: f["X"].__delitem__("data"))
    _refused(path, "X")


def test_an_index_stored_another_way_says_so(tmp_path):
    def as_categorical(f):
        del f["obs"]["_index"]
        group = f["obs"].create_group("_index")
        group.attrs["encoding-type"] = "categorical"
        group.create_dataset("categories", data=np.array(["a", "b"], dtype=object),
                             dtype=__import__("h5py").string_dtype())
        group.create_dataset("codes", data=np.array([0, 1, 0], dtype=np.int8))

    _refused(_break(write_h5ad(tmp_path / "f.h5ad"), as_categorical), "obs")


def test_a_normalization_field_holding_many_values_says_so(tmp_path):
    def many(f):
        del f["uns"]["normalization"]["target_sum"]
        f["uns"]["normalization"].create_dataset("target_sum", data=np.array([1, 2, 3]))

    _refused(_break(write_h5ad(tmp_path / "f.h5ad"), many), "normalization")


def test_a_counts_layer_of_another_shape_is_refused(tmp_path):
    _refused(write_h5ad(tmp_path / "f.h5ad", counts=np.ones((3, 3))),
             r"layers\['counts'\] is 3 x 3; X is 3 x 4")


def test_a_negative_count_is_refused(tmp_path):
    counts = np.ones((3, 4))
    counts[2, 1] = -1
    _refused(write_h5ad(tmp_path / "f.h5ad", counts=counts), "negative")


@pytest.mark.parametrize("block, match", [
    ({**NORMALIZATION, "transform": "log10"}, "transform is 'log10'"),
    ({**NORMALIZATION, "scaling": "cpm"}, "scaling is 'cpm'"),
    ({"transform": "log1p", "scaling": "library_size"}, "target_sum"),
    ({"transform": "log1p", "scaling": "library_size", "target_sum": 0}, "target_sum"),
    ({"transform": "none", "scaling": "other"}, "description"),
    ({**NORMALIZATION, "counts_layer": "raw"}, "counts_layer 'raw'"),
    ({"scaling": "none"}, "transform"),
], ids=["transform", "scaling", "no-target-sum", "zero-target-sum", "other-undescribed",
        "counts-layer-absent", "no-transform"])
def test_a_malformed_normalization_is_refused(tmp_path, block, match):
    _refused(write_h5ad(tmp_path / "f.h5ad", normalization=block), match)


def test_normalization_problems_are_named_without_a_file():
    assert fmt.normalization_problem(NORMALIZATION, layers={"counts"}) is None
    assert "target_sum" in fmt.normalization_problem(
        {"transform": "log1p", "scaling": "library_size"}, layers=set())


def test_the_check_says_how_to_install_its_extra(tmp_path, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "h5py", None)
    with pytest.raises(fmt.MissingExtra, match=r"bloomctl\[scrna\]"):
        fmt.check_structure(tmp_path / "any.h5ad")


def test_a_file_whose_layers_is_not_a_group_is_refused_not_crashed(tmp_path):
    """I4: this read sat outside the wrapping, so a bad file produced a raw AttributeError."""
    import h5py

    path = write_h5ad(tmp_path / "x.h5ad")
    with h5py.File(path, "a") as f:
        del f["layers"]
        f.create_dataset("layers", data=[1, 2, 3])
    _refused(path, "layers")


def test_a_refusal_never_ends_in_a_bare_colon():
    """I5: an exception whose str() is empty left the message dangling after 'could not be read:'."""
    with pytest.raises(fmt.FormatError) as caught:
        with fmt._reading("obs"):
            raise KeyError()
    assert not str(caught.value).rstrip().endswith(":")
    assert "KeyError" in str(caught.value)


def test_a_fault_here_is_not_reported_as_a_fact_about_the_file():
    """I5: bare `except Exception` swallowed MemoryError and our own bugs alike."""
    with pytest.raises(MemoryError):
        with fmt._reading("X"):
            raise MemoryError()


def test_coordinates_too_large_to_store_are_refused(tmp_path):
    """I2: the column is double precision but the explorer casts to REAL when reading."""
    cells = np.arange(24, dtype=float).reshape(6, 4)
    coords = np.zeros((6, 2), dtype="float64")
    coords[:, 0] = np.arange(6) * 1.0
    coords[3, 1] = 1e300
    _refused(write_h5ad(tmp_path / "x.h5ad", x=cells, umap=coords), "too large")


def test_cells_piled_on_one_point_are_refused(tmp_path):
    """I2: an obsm allocated and never filled is finite, two-dimensional and the right length."""
    cells = np.arange(500 * 4, dtype=float).reshape(500, 4)
    coords = np.zeros((500, 2), dtype="float64")
    coords[:, 0] = np.arange(500) * 1.0
    coords[:3, 0] = 7.0        # 3 of 500 share a point, over the loader's 0.1%
    coords[:3, 1] = 7.0
    _refused(write_h5ad(tmp_path / "x.h5ad", x=cells, umap=coords), "single point")


def test_the_umap_can_be_named(tmp_path):
    """I2: the loader takes --umap-key, so a file it loads was refused here for its name."""
    path = write_h5ad(tmp_path / "x.h5ad", umap_key="umap")
    _refused(path, "no obsm")
    assert fmt.check_structure(path, umap_key="umap").n_cells > 0


def test_a_file_that_points_outside_itself_is_refused(tmp_path):
    """I9: h5py follows an external link, and the refusal echoes what it found."""
    import h5py

    secret = tmp_path / "secret.h5"
    with h5py.File(secret, "w") as s:
        s.create_dataset("value", data=[1, 2, 3])
    path = write_h5ad(tmp_path / "x.h5ad")
    with h5py.File(path, "a") as f:
        f["uns"]["elsewhere"] = h5py.ExternalLink(str(secret), "/value")
    _refused(path, "outside itself")
