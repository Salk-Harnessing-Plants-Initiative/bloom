"""
Unit tests for `scripts/ingest_scrnaseq_counts.py`.

The per-gene vectors are served to the browser as bare arrays with no cell
identifiers in them, so almost everything that can go wrong here is silent: a
gene written under another gene's name, a vector in the wrong order, or values
read back at the wrong width. These build real `.h5ad` files and check the
refusals fire and the vectors come out exactly right.

The database and storage writes are covered by
tests/integration/test_scrna_ingest_counts.py, which has both.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import anndata
import numpy as np
import pandas as pd
import pytest
from scipy import sparse

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "ingest_scrnaseq_counts.py"
CELL_SCRIPT = REPO_ROOT / "scripts" / "ingest_scrnaseq.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def counts():
    return _load(SCRIPT, "ingest_scrnaseq_counts")


@pytest.fixture(scope="module")
def cells():
    return _load(CELL_SCRIPT, "ingest_scrnaseq")


def write_h5ad(path: Path, matrix, genes: list[str] | None = None,
               sparse_matrix: bool = True) -> Path:
    """A dataset with the given expression values, laid out as the real file is:
    cells down, genes across."""
    matrix = np.asarray(matrix, dtype="float32")
    n_cells, n_genes = matrix.shape
    names = genes or [f"AT1G{i:05d}" for i in range(n_genes)]
    adata = anndata.AnnData(
        X=sparse.csr_matrix(matrix) if sparse_matrix else matrix,
        obs=pd.DataFrame(index=[f"CELL{i}" for i in range(n_cells)]),
        var=pd.DataFrame(index=names),
    )
    adata.write_h5ad(path)
    return path


# --------------------------------------------------------------------------- #
# The checksum has to mean the same thing in both scripts
# --------------------------------------------------------------------------- #


def test_both_scripts_compute_the_same_checksum(counts, cells, tmp_path):
    """The whole safety of this step is that the cells and the counts came from
    one file. One script records the checksum and the other refuses unless it
    matches, so the two have to agree byte for byte."""
    path = write_h5ad(tmp_path / "sum.h5ad", np.eye(4, dtype="float32"))
    assert counts.checksum(path) == cells.checksum(path)

    path.write_bytes(path.read_bytes() + b"x")
    assert counts.checksum(path) == cells.checksum(path)


def test_the_checksum_changes_with_the_content(counts, tmp_path):
    a = write_h5ad(tmp_path / "a.h5ad", np.eye(4, dtype="float32"))
    before = counts.checksum(a)
    write_h5ad(tmp_path / "a.h5ad", np.ones((4, 4), dtype="float32"))
    assert counts.checksum(a) != before


# --------------------------------------------------------------------------- #
# What it reads
# --------------------------------------------------------------------------- #


def test_a_gene_vector_is_the_gene_column_in_cell_order(counts, tmp_path):
    """The array is paired with the cells by position and nothing else, so the
    order is the whole contract."""
    matrix = np.array([[1, 10], [2, 20], [3, 30], [4, 40]], dtype="float32")
    path = write_h5ad(tmp_path / "order.h5ad", matrix, ["GENE_A", "GENE_B"])
    read = counts.read_genes(path, {})
    assert list(counts.gene_vector(read["by_gene"], 0)) == [1, 2, 3, 4]
    assert list(counts.gene_vector(read["by_gene"], 1)) == [10, 20, 30, 40]


def test_a_gene_vector_is_float32_whatever_the_file_holds(counts, tmp_path):
    """The browser reads the bytes back as float32; storing anything wider would
    shift every value it reads after the first."""
    matrix = np.array([[1.5, 2.5]], dtype="float32")
    path = write_h5ad(tmp_path / "width.h5ad", matrix)
    read = counts.read_genes(path, {})
    vector = counts.gene_vector(read["by_gene"], 0)
    assert vector.dtype == np.dtype("<f4")
    assert len(vector.tobytes()) == 4 * matrix.shape[0]


def test_a_dense_matrix_reads_the_same_as_a_sparse_one(counts, tmp_path):
    matrix = np.array([[0, 1], [2, 0], [3, 4]], dtype="float32")
    a = counts.read_genes(write_h5ad(tmp_path / "s.h5ad", matrix), {})
    b = counts.read_genes(
        write_h5ad(tmp_path / "d.h5ad", matrix, sparse_matrix=False), {}
    )
    for column in (0, 1):
        assert list(counts.gene_vector(a["by_gene"], column)) == \
            list(counts.gene_vector(b["by_gene"], column))


def test_a_gene_with_no_expression_is_still_a_vector(counts, tmp_path):
    """Nearly a fifth of the real file's genes are zero everywhere. They are
    written, so colouring by one shows every cell at zero rather than failing."""
    matrix = np.array([[0, 1], [0, 2]], dtype="float32")
    path = write_h5ad(tmp_path / "zero.h5ad", matrix)
    read = counts.read_genes(path, {})
    assert list(counts.gene_vector(read["by_gene"], 0)) == [0, 0]


# --------------------------------------------------------------------------- #
# What it refuses
# --------------------------------------------------------------------------- #


def test_a_missing_file_is_refused(counts, tmp_path):
    with pytest.raises(counts.IngestError, match="no such file"):
        counts.read_genes(tmp_path / "nope.h5ad", {})


def test_a_file_with_no_cells_is_refused(counts, tmp_path):
    path = write_h5ad(tmp_path / "nocells.h5ad", np.zeros((0, 3), dtype="float32"))
    with pytest.raises(counts.IngestError, match="holds no cells"):
        counts.read_genes(path, {})


def test_a_file_with_no_genes_is_refused(counts, tmp_path):
    path = write_h5ad(tmp_path / "nogenes.h5ad", np.zeros((3, 0), dtype="float32"))
    with pytest.raises(counts.IngestError, match="holds no genes"):
        counts.read_genes(path, {})


def test_two_genes_with_one_name_are_refused(counts, tmp_path):
    """They would write to one object, and the second would replace the first
    for both of them -- so both genes would then colour identically."""
    path = write_h5ad(tmp_path / "dup.h5ad", np.eye(2, dtype="float32"),
                      ["SAME", "SAME"])
    with pytest.raises(counts.IngestError, match="more than once"):
        counts.read_genes(path, {})


@pytest.mark.parametrize("name", ["with/slash", "with space", "with?query",
                                  "with#hash", "..", "with%20escape"])
def test_a_gene_name_that_cannot_be_a_path_is_refused(counts, tmp_path, name):
    path = write_h5ad(tmp_path / "unsafe.h5ad", np.eye(2, dtype="float32"),
                      ["FINE", name])
    with pytest.raises(counts.IngestError, match="cannot be part of an object path"):
        counts.read_genes(path, {})


def test_a_blank_gene_name_is_refused(counts, tmp_path):
    path = write_h5ad(tmp_path / "blank.h5ad", np.eye(2, dtype="float32"),
                      ["FINE", "   "])
    with pytest.raises(counts.IngestError, match="blank"):
        counts.read_genes(path, {})


# --------------------------------------------------------------------------- #
# The expectations, which are how a known gene gets pinned at ingest
# --------------------------------------------------------------------------- #


def test_an_expectation_that_holds_is_accepted(counts, tmp_path):
    matrix = np.array([[1, 0], [2, 0], [0, 0]], dtype="float32")
    path = write_h5ad(tmp_path / "expect.h5ad", matrix, ["HIT", "MISS"])
    read = counts.read_genes(path, {"HIT": 2, "MISS": 0})
    assert read["expectations"] == {"HIT": 2, "MISS": 0}


def test_an_expectation_that_fails_refuses_the_whole_load(counts, tmp_path):
    matrix = np.array([[1, 0], [2, 0], [0, 0]], dtype="float32")
    path = write_h5ad(tmp_path / "wrong.h5ad", matrix, ["HIT", "MISS"])
    with pytest.raises(counts.IngestError, match="HIT is non-zero in 2 cells"):
        counts.read_genes(path, {"HIT": 3})


def test_an_expectation_naming_an_absent_gene_is_refused(counts, tmp_path):
    path = write_h5ad(tmp_path / "absent.h5ad", np.eye(2, dtype="float32"))
    with pytest.raises(counts.IngestError, match="not in this file"):
        counts.read_genes(path, {"NOSUCHGENE": 1})


@pytest.mark.parametrize("bad", ["GENE", "GENE=", "=3", "GENE=three", "GENE=-1"])
def test_a_malformed_expectation_is_refused(counts, bad):
    with pytest.raises(counts.IngestError, match="wants GENE=COUNT"):
        counts.parse_expectations([bad])


def test_expectations_are_collected_from_every_flag(counts):
    assert counts.parse_expectations(["A=1", "B=22"]) == {"A": 1, "B": 22}
    assert counts.parse_expectations([]) == {}


# --------------------------------------------------------------------------- #
# The path the explorer builds for itself
# --------------------------------------------------------------------------- #


def test_the_object_path_is_the_one_the_explorer_fetches(counts):
    """web/components/expression-lib/scrna-client.ts builds this path from the
    dataset and gene names, so it is a contract rather than a choice."""
    assert counts.object_path("MYB41 transgene", "AT4G28110.Fusion") == \
        "counts/MYB41 transgene/AT4G28110.Fusion.bin"


def test_every_gene_gets_its_own_path(counts, tmp_path):
    path = write_h5ad(tmp_path / "paths.h5ad", np.eye(3, dtype="float32"),
                      ["A", "B", "C"])
    read = counts.read_genes(path, {})
    paths = {counts.object_path("d", g) for g in read["names"]}
    assert len(paths) == 3


# --------------------------------------------------------------------------- #
# The command line
# --------------------------------------------------------------------------- #


def test_a_dry_run_needs_no_credentials_and_writes_nothing(counts, tmp_path,
                                                           monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    matrix = np.array([[1, 0], [2, 0]], dtype="float32")
    path = write_h5ad(tmp_path / "dry.h5ad", matrix, ["HIT", "MISS"])
    code = counts.main([
        "--h5ad", str(path), "--dataset-name", "d", "--species-id", "1",
        "--expect-nonzero", "HIT=2", "--dry-run",
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "2 genes over 2 cells" in out
    assert "HIT is non-zero in 2 cells, as expected" in out


def test_a_dry_run_still_refuses_a_failed_expectation(counts, tmp_path, capsys):
    matrix = np.array([[1, 0], [2, 0]], dtype="float32")
    path = write_h5ad(tmp_path / "dryfail.h5ad", matrix, ["HIT", "MISS"])
    code = counts.main([
        "--h5ad", str(path), "--dataset-name", "d", "--species-id", "1",
        "--expect-nonzero", "HIT=9", "--dry-run",
    ])
    assert code == 1
    assert "expected 9" in capsys.readouterr().err


def test_writing_needs_all_three_credentials(counts, tmp_path, monkeypatch,
                                             capsys):
    """Two of the three would get part way and then stop, having already
    written objects with nothing recording them."""
    path = write_h5ad(tmp_path / "creds.h5ad", np.eye(2, dtype="float32"))
    for present in ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_SERVICE_KEY"):
        for name in ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_SERVICE_KEY"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv(present, "set")
        code = counts.main([
            "--h5ad", str(path), "--dataset-name", "d", "--species-id", "1",
        ])
        assert code == 1
        assert "are all" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["with/slash", "../escape", "..", ".",
                                  "with?query", "with#fragment", "  "])
def test_a_dataset_name_that_cannot_be_a_path_is_refused(counts, name):
    """It is a path segment too. A slash would put the whole dataset under a
    different prefix, possibly over another dataset's genes."""
    with pytest.raises(counts.IngestError, match="blank|cannot be part of an object path"):
        counts.check_dataset_name(name)


def test_the_real_dataset_name_is_accepted(counts):
    """It has a space in it, so the rule cannot simply be accession characters."""
    counts.check_dataset_name("MYB41 transgene")


def test_the_dataset_name_is_checked_before_anything_is_read(counts, tmp_path,
                                                             capsys):
    path = write_h5ad(tmp_path / "badname.h5ad", np.eye(2, dtype="float32"))
    code = counts.main([
        "--h5ad", str(path), "--dataset-name", "../elsewhere",
        "--species-id", "1", "--dry-run",
    ])
    assert code == 1
    assert "cannot be part of an object path" in capsys.readouterr().err
