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
import json

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
def test_a_gene_is_keyed_by_cell_index_in_cell_order(counts, tmp_path):
    """The index is the identity -- nothing else in the object says which cell a
    value belongs to -- so the numbering is the whole contract."""
    matrix = np.array([[1, 10], [2, 20], [3, 30], [4, 40]], dtype="float32")
    path = write_h5ad(tmp_path / "order.h5ad", matrix, ["GENE_A", "GENE_B"])
    read = counts.read_genes(path, {})
    assert counts.gene_counts(read["by_gene"], 0) == \
        {"0": 1.0, "1": 2.0, "2": 3.0, "3": 4.0}
    assert counts.gene_counts(read["by_gene"], 1) == \
        {"0": 10.0, "1": 20.0, "2": 30.0, "3": 40.0}


def test_only_the_cells_with_expression_are_stored(counts, tmp_path):
    """Single-cell data is mostly zeros. Storing them would multiply the size of
    every object for no information -- an absent index reads as zero."""
    matrix = np.array([[0, 1], [2, 0], [0, 4]], dtype="float32")
    path = write_h5ad(tmp_path / "sparse.h5ad", matrix)
    read = counts.read_genes(path, {})
    assert counts.gene_counts(read["by_gene"], 0) == {"1": 2.0}
    assert counts.gene_counts(read["by_gene"], 1) == {"0": 1.0, "2": 4.0}


def test_the_keys_are_text_as_json_requires(counts, tmp_path):
    """The stored object is JSON, whose keys are strings. A reader doing
    Number(key) gets the index back; an integer key would not survive the trip."""
    matrix = np.array([[5.0]], dtype="float32")
    read = counts.read_genes(write_h5ad(tmp_path / "keys.h5ad", matrix), {})
    got = counts.gene_counts(read["by_gene"], 0)
    assert all(isinstance(k, str) for k in got)
    assert json.loads(json.dumps(got)) == got


def test_a_dense_matrix_reads_the_same_as_a_sparse_one(counts, tmp_path):
    matrix = np.array([[0, 1], [2, 0], [3, 4]], dtype="float32")
    a = counts.read_genes(write_h5ad(tmp_path / "s.h5ad", matrix), {})
    b = counts.read_genes(
        write_h5ad(tmp_path / "d.h5ad", matrix, sparse_matrix=False), {}
    )
    for column in (0, 1):
        assert counts.gene_counts(a["by_gene"], column) == \
            counts.gene_counts(b["by_gene"], column)


def test_a_gene_with_no_expression_stores_an_empty_object(counts, tmp_path):
    """Nearly a fifth of the real file's genes are zero everywhere. They are
    still written, so colouring by one shows every cell at zero rather than
    failing to find the object at all."""
    matrix = np.array([[0, 1], [0, 2]], dtype="float32")
    path = write_h5ad(tmp_path / "zero.h5ad", matrix)
    read = counts.read_genes(path, {})
    assert counts.gene_counts(read["by_gene"], 0) == {}


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


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
@pytest.mark.parametrize("sparse_matrix", [True, False])
def test_a_value_that_is_not_finite_is_refused_naming_the_gene(
    counts, tmp_path, bad, sparse_matrix
):
    """The explorer cannot colour a cell by NaN or infinity, and JSON has no
    spelling for either."""
    matrix = np.array([[1, 0], [2, bad]], dtype="float32")
    path = write_h5ad(tmp_path / "bad.h5ad", matrix, ["FINE", "BROKEN"],
                      sparse_matrix=sparse_matrix)
    with pytest.raises(counts.IngestError, match=r"not finite.*BROKEN"):
        counts.read_genes(path, {})


def test_a_dry_run_refuses_a_value_that_is_not_finite(counts, tmp_path, capsys):
    matrix = np.array([[1, 0], [np.nan, 0]], dtype="float32")
    path = write_h5ad(tmp_path / "drynan.h5ad", matrix, ["NANGENE", "MISS"])
    code = counts.main(["--h5ad", str(path), "--dataset-name", "d",
                        "--species-id", "1", "--dry-run"])
    assert code == 1
    assert "NANGENE" in capsys.readouterr().err


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


def test_the_object_path_is_the_one_the_cli_writes(counts):
    """counts/<name>_<dataset id>_/, as the bloom-js CLI writes it: the id keeps two
    datasets with one name apart, the name keeps a bucket listing readable."""
    assert counts.object_path("MYB41 transgene", 7, "AT4G28110.Fusion") == \
        "counts/MYB41_transgene_7_/AT4G28110.Fusion.json"


@pytest.mark.parametrize("name,cleaned", [
    ("  MYB41 transgene ", "MYB41_transgene"), ("a  b\tc", "a_b_c"),
    ("pennycress_data2.json", "pennycress_data2"),
])
def test_the_dataset_name_is_cleaned_as_the_cli_cleans_it(counts, name, cleaned):
    assert counts.object_path(name, 3, "G") == f"counts/{cleaned}_3_/G.json"


def test_two_datasets_with_one_name_get_separate_paths(counts):
    assert counts.object_path("d", 1, "G") != counts.object_path("d", 2, "G")


def test_every_gene_gets_its_own_path(counts, tmp_path):
    path = write_h5ad(tmp_path / "paths.h5ad", np.eye(3, dtype="float32"),
                      ["A", "B", "C"])
    read = counts.read_genes(path, {})
    paths = {counts.object_path("d", 1, g) for g in read["names"]}
    assert len(paths) == 3


# --------------------------------------------------------------------------- #
# The command line
# --------------------------------------------------------------------------- #


def _no_network(counts, monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a dry run must not reach the site")
    monkeypatch.setattr(counts.ingest_api, "resolve_api", refuse)
    monkeypatch.setattr(counts.ingest_api, "sign_in", refuse)
    monkeypatch.delenv("BLOOM_PASSWORD", raising=False)


def test_a_dry_run_needs_no_account_and_says_what_it_would_write(
    counts, tmp_path, monkeypatch, capsys
):
    _no_network(counts, monkeypatch)
    matrix = np.array([[1, 0], [2, 0]], dtype="float32")
    path = write_h5ad(tmp_path / "dry.h5ad", matrix, ["HIT", "MISS"])
    code = counts.main([
        "--h5ad", str(path), "--dataset-name", " d ", "--species-id", "1",
        "--expect-nonzero", "HIT=2", "--dry-run",
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "2 genes over 2 cells" in out
    assert "HIT is non-zero in 2 cells, as expected" in out
    assert "would write 2 objects under counts/d_<dataset id>_/" in out
    assert "dry run — nothing written" in out


def test_a_dry_run_still_refuses_a_failed_expectation(counts, tmp_path, capsys):
    matrix = np.array([[1, 0], [2, 0]], dtype="float32")
    path = write_h5ad(tmp_path / "dryfail.h5ad", matrix, ["HIT", "MISS"])
    code = counts.main([
        "--h5ad", str(path), "--dataset-name", "d", "--species-id", "1",
        "--expect-nonzero", "HIT=9", "--dry-run",
    ])
    assert code == 1
    assert "expected 9" in capsys.readouterr().err


def _signed_in(counts, monkeypatch):
    api = counts.ingest_api
    monkeypatch.setattr(api, "resolve_api", lambda server, url, key, **_: ("http://x/api", "k"))
    monkeypatch.setattr(api, "sign_in", lambda url, key, email, password, **_: api.Session(
        lambda: (None, "bloom_writer", "u1"), None, "bloom_writer", "u1"))
    monkeypatch.setenv("BLOOM_PASSWORD", "pw")
    for var in ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_SERVICE_KEY"):
        monkeypatch.delenv(var, raising=False)


def _run(counts, tmp_path, *extra):
    path = write_h5ad(tmp_path / "run.h5ad", np.eye(2, dtype="float32"))
    return counts.main(["--h5ad", str(path), "--dataset-name", "d", "--species-id",
                        "1", "--server", "https://x", *extra])


def test_writing_needs_an_email(counts, tmp_path, monkeypatch, capsys):
    _signed_in(counts, monkeypatch)
    assert _run(counts, tmp_path) == 1
    assert "--email" in capsys.readouterr().err


@pytest.mark.parametrize("result,said", [
    ((7, 2, 0), "dataset 7: 2 genes over 2 cells"),
    ((7, 0, 2), "already loaded"),
])
def test_a_load_needs_no_database_url_and_says_what_happened(
    counts, tmp_path, monkeypatch, capsys, result, said
):
    _signed_in(counts, monkeypatch)
    seen = {}

    def recorder(writer, name, species_id, genes):
        seen.update(name=name, species_id=species_id)
        return result
    monkeypatch.setattr(counts, "load", recorder)
    assert _run(counts, tmp_path, "--email", "me@salk.edu") == 0
    assert said in capsys.readouterr().out
    assert seen == {"name": "d", "species_id": 1}


def test_the_wait_is_checked_before_signing_in(counts, tmp_path, monkeypatch, capsys):
    _signed_in(counts, monkeypatch)
    def no_sign_in(*a, **k):
        raise AssertionError("signed in during the wait")
    monkeypatch.setattr(counts.ingest_api, "sign_in", no_sign_in)
    api = counts.ingest_api
    api.Marker(api.marker_path(tmp_path / "run.h5ad", "d")).record("upload G1")
    assert _run(counts, tmp_path, "--email", "me@salk.edu") == 1
    assert "seconds" in capsys.readouterr().err


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
