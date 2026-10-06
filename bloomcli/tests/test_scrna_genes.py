"""Reading each gene's values out of an h5ad, a block of genes at a time.

The values reach the browser as bare objects keyed by cell position, so almost everything that
can go wrong is silent: a gene under another's name, a value at the wrong cell. These build
real files in every layout and check the values come out exactly, however the blocks fall.
"""

from __future__ import annotations

from pathlib import Path

import anndata
import numpy as np
import pandas as pd
import pytest
from scipy import sparse

from bloomctl.scrna import _genes
from bloomctl.scrna._writer import LoadError

MATRIX = np.array([
    [0.0, 1.5, 0.0, 2.0, 0.0],
    [0.7, 0.0, 0.0, 0.0, 0.0],
    [0.0, 0.0, 3.1, 0.2, 0.0],
    [0.4, 0.0, 0.0, 0.9, 0.0],
], dtype="float32")


def write_h5ad(path: Path, matrix=MATRIX, genes=None, layout: str = "csr") -> Path:
    """Cells down, genes across, stored as the given layout."""
    matrix = np.asarray(matrix, dtype="float32")
    n_cells, n_genes = matrix.shape
    stored = {"csr": sparse.csr_matrix, "csc": sparse.csc_matrix, "dense": np.asarray}[layout]
    anndata.AnnData(
        X=stored(matrix),
        obs=pd.DataFrame(index=[f"CELL{i}" for i in range(n_cells)]),
        var=pd.DataFrame(index=genes or [f"AT1G{i:05d}" for i in range(n_genes)]),
    ).write_h5ad(path)
    return path


def expected(matrix=MATRIX) -> dict[int, dict[str, float]]:
    return {j: {str(i): float(matrix[i, j]) for i in range(matrix.shape[0]) if matrix[i, j]}
            for j in range(matrix.shape[1])}


def values(path: Path, **kwargs) -> dict[int, dict[str, float]]:
    return dict(_genes.gene_values(path, _genes.read_names(path), **kwargs))


# --- the values ---------------------------------------------------------------------------


@pytest.mark.parametrize("layout", ["csr", "csc", "dense"])
def test_every_layout_gives_each_gene_its_values_by_cell_position(tmp_path, layout):
    assert values(write_h5ad(tmp_path / "f.h5ad", layout=layout)) == expected()


def test_only_cells_with_expression_are_kept_and_keys_are_text(tmp_path):
    got = values(write_h5ad(tmp_path / "f.h5ad"))
    assert got[1] == {"0": 1.5}
    assert all(isinstance(k, str) for gene in got.values() for k in gene)


def test_a_gene_expressed_nowhere_has_no_values(tmp_path):
    assert values(write_h5ad(tmp_path / "f.h5ad"))[4] == {}


@pytest.mark.parametrize("layout", ["csr", "csc", "dense"])
def test_the_smallest_blocks_and_slices_give_the_same_values(tmp_path, monkeypatch, layout):
    monkeypatch.setattr(_genes, "BLOCK_VALUES", 1)
    monkeypatch.setattr(_genes, "SLICE_VALUES", 1)
    assert values(write_h5ad(tmp_path / "f.h5ad", layout=layout)) == expected()


def test_a_larger_matrix_is_read_the_same_in_blocks_as_whole(tmp_path, monkeypatch):
    rng = np.random.default_rng(7)
    matrix = rng.random((60, 40)).astype("float32")
    matrix[matrix < 0.8] = 0
    path = write_h5ad(tmp_path / "f.h5ad", matrix=matrix)
    whole = values(path)
    monkeypatch.setattr(_genes, "BLOCK_VALUES", 7)
    monkeypatch.setattr(_genes, "SLICE_VALUES", 13)
    assert values(path) == whole == expected(matrix)


@pytest.mark.parametrize("layout", ["csr", "csc", "dense"])
def test_scattered_genes_are_read_on_their_own(tmp_path, layout):
    got = values(write_h5ad(tmp_path / "f.h5ad", layout=layout), only=[3, 0])
    assert got == {3: expected()[3], 0: expected()[0]}


@pytest.mark.parametrize("bad", [np.nan, np.inf])
def test_a_value_that_is_not_finite_is_refused_naming_the_gene(tmp_path, bad):
    matrix = MATRIX.copy()
    matrix[2, 3] = bad
    with pytest.raises(LoadError, match="AT1G00003 holds a value that is not finite"):
        values(write_h5ad(tmp_path / "f.h5ad", matrix=matrix))


# --- the names ------------------------------------------------------------------------------


def test_the_annotation_release_is_stripped_from_gene_names(tmp_path):
    path = write_h5ad(tmp_path / "f.h5ad", genes=[f"AT1G0000{i}.Araport11.447" for i in range(5)])
    assert _genes.read_names(path)[0] == "AT1G00000"


def test_two_releases_of_one_gene_are_refused(tmp_path):
    genes = ["AT1G00001.Araport11.1", "AT1G00001.Araport11.2", "B", "C", "D"]
    with pytest.raises(LoadError, match="appear more than once.*AT1G00001"):
        _genes.read_names(write_h5ad(tmp_path / "f.h5ad", genes=genes))


@pytest.mark.parametrize("name", ["a/b", "..", "x y", "g\\h"])
def test_a_gene_name_that_cannot_be_a_path_is_refused(tmp_path, name):
    genes = [name, "B", "C", "D", "E"]
    with pytest.raises(LoadError, match="cannot be part of an object path"):
        _genes.read_names(write_h5ad(tmp_path / "f.h5ad", genes=genes))


def test_a_file_with_no_genes_is_refused(tmp_path):
    path = write_h5ad(tmp_path / "f.h5ad", matrix=np.zeros((3, 0)), genes=[])
    with pytest.raises(LoadError, match="holds no genes"):
        _genes.read_names(path)


# --- --expect-nonzero -----------------------------------------------------------------------


def test_an_expectation_that_holds_is_accepted(tmp_path):
    path = write_h5ad(tmp_path / "f.h5ad")
    _genes.check_expectations(path, _genes.read_names(path), {"AT1G00003": 3})


def test_an_expectation_that_fails_is_refused_with_both_counts(tmp_path):
    path = write_h5ad(tmp_path / "f.h5ad")
    with pytest.raises(LoadError, match="AT1G00003 is non-zero in 3 cells, expected 232"):
        _genes.check_expectations(path, _genes.read_names(path), {"AT1G00003": 232})


def test_an_expectation_naming_an_absent_gene_is_refused(tmp_path):
    path = write_h5ad(tmp_path / "f.h5ad")
    with pytest.raises(LoadError, match="not a gene in this file"):
        _genes.check_expectations(path, _genes.read_names(path), {"AT9G99999": 1})


def test_an_expectation_may_name_a_gene_with_its_release():
    assert _genes.parse_expectations(["AT4G28110.Araport11.1=232"]) == {"AT4G28110": 232}


@pytest.mark.parametrize("bad", ["AT1G1", "AT1G1=", "=5", "AT1G1=x", "AT1G1=-1"])
def test_a_malformed_expectation_is_refused(bad):
    with pytest.raises(LoadError, match="GENE=COUNT"):
        _genes.parse_expectations([bad])
