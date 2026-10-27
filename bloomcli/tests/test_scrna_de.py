"""`scrna hdf5 upload --de-key`: a one-vs-rest differential expression stored in the file (scanpy's
rank_genes_groups) is loaded with the dataset, as one DE run, a comparison per cluster, every
tested gene, and each cluster's statistics, in the shapes the expression page reads; refused
unless it compares the annotation's clusters with the rest; continued, not repeated, by a
resumed load. Without the option, no DE is loaded."""

from __future__ import annotations

import h5py
import numpy as np
import pandas as pd
import pytest
from scrna_fake_db import FakeClient
from scrna_fixtures import write_h5ad
from test_scrna_cli import _run

from bloomctl.scrna import _de, _load
from bloomctl.scrna._checks import check_nothing_later
from bloomctl.scrna._writer import LoadError, Marker, Writer

GENES = ["G1", "G2", "G3"]
CLUSTERS = ["A", "B"]
CELLS = {"n_cells": 4, "n_genes": 3, "x": [0.0, 2.0, 10.0, 12.0], "y": [1.0, 3.0, 5.0, 7.0],
         "labels": ["A", "A", "B", "B"], "samples": ["S"] * 4, "levels": CLUSTERS,
         "barcodes": [f"BC{i}" for i in range(4)], "sources": {}}
OPTIONS = {"annotation": "leiden", "sample_column": "sample", "umap_key": "X_umap",
           "source_column": None, "expression_units": "log1p normalised counts"}


def _records(values: dict[str, list], dtype) -> np.ndarray:
    """A scanpy-style record array: one field per cluster, one row per rank."""
    return np.rec.fromarrays([np.asarray(values[c], dtype=dtype) for c in CLUSTERS],
                             names=CLUSTERS)


def _stored(reference="rest", groupby="leiden", pts=True, **overrides) -> dict:
    """A rank_genes_groups result for clusters A and B over genes G1-G3."""
    stored = {
        "params": {"groupby": groupby, "reference": reference, "method": "wilcoxon",
                   "use_raw": False, "corr_method": "benjamini-hochberg"},
        "names": _records({"A": ["G1", "G2", "G3"], "B": ["G3", "G1", "G2"]}, object),
        "logfoldchanges": _records({"A": [3.0, np.nan, -1.0], "B": [2.0, np.inf, 0.5]}, float),
        "pvals": _records({"A": [1e-6, 0.02, np.nan], "B": [1e-4, 0.5, 0.9]}, float),
        "pvals_adj": _records({"A": [1e-5, 0.01, 0.5], "B": [1e-3, 1.2, 0.95]}, float),
    }
    if pts:
        stored["pts"] = pd.DataFrame({"A": [0.9, 0.4, 0.1], "B": [0.2, 0.3, 0.8]}, index=GENES)
        stored["pts_rest"] = pd.DataFrame({"A": [0.2, 0.3, 0.8], "B": [0.9, 0.4, 0.1]},
                                          index=GENES)
    stored.update(overrides)
    return stored


def _result(**kwargs) -> _de.Result:
    return _de._result(_stored(**kwargs), "de", "leiden", CELLS, {"G1": "ABC1"})


def _writer(client, tmp_path):
    return Writer(client, lambda: client, Marker(tmp_path / "m.json", wait_s=0))


def _client_with_genes(dataset_id: int) -> FakeClient:
    client = FakeClient({"species": [{"id": 1, "common_name": "Arabidopsis"}]})
    client.tables["scrna_genes"] = [
        {"id": 50 + i, "dataset_id": dataset_id, "gene_name": g} for i, g in enumerate(GENES)]
    return client


def _load_with(client, tmp_path, de):
    # The fake's first insert is the dataset, so it gets id 1000; its genes are seeded there.
    return _load.load(_writer(client, tmp_path), "Root atlas", 1, CELLS, "sha-1", OPTIONS,
                      create=True, de=de)


# --- reading the result -------------------------------------------------------


def test_each_cluster_is_compared_with_the_rest_with_its_size():
    result = _result()
    assert [(c.cluster, c.n_group1, c.n_group2) for c in result.comparisons] == [
        ("A", 2, 2), ("B", 2, 2)]
    assert result.method == "scanpy-wilcoxon-one-vs-rest"


def test_gene_values_are_stored_as_the_table_takes_them():
    a, b = _result().comparisons
    g1, g2 = a.genes  # G3 has no p-value, so it wasn't tested and is left out
    assert g1 == {"gene": "G1", "log2fc": 3.0, "pvalue": 1e-6, "fdr": 1e-5,
                  "pct_1": 0.9, "pct_2": 0.2}
    assert g2["log2fc"] is None, "a NaN fold change is stored as null"
    assert g2["fdr"] == 0.02, "fdr is raised to at least the p-value"
    assert b.genes[1]["log2fc"] is None, "an infinite fold change is stored as null"
    assert b.genes[1]["fdr"] == 1.0, "an adjusted p-value above 1 is clipped"


def test_without_pts_the_shares_are_null():
    (gene, *_) = _result(pts=False).comparisons[0].genes
    assert gene["pct_1"] is None and gene["pct_2"] is None


@pytest.mark.parametrize("kwargs, message", [
    ({"reference": "B"}, "not each with the rest"),
    ({"groupby": "cell_type"}, "groups cells by 'cell_type'"),
])
def test_a_result_that_isnt_one_vs_rest_on_the_annotation_is_refused(kwargs, message):
    with pytest.raises(LoadError, match=message):
        _result(**kwargs)


def test_a_result_for_an_unknown_cluster_is_refused():
    stored = _stored()
    stored["names"] = np.rec.fromarrays([np.asarray(GENES, dtype=object)], names=["Z"])
    with pytest.raises(LoadError, match="'Z'"):
        _de._result(stored, "de", "leiden", CELLS)


@pytest.mark.parametrize("part", ["params", "names", "pvals", "pvals_adj", "logfoldchanges"])
def test_a_result_missing_a_part_is_refused(part):
    stored = _stored()
    del stored[part]
    with pytest.raises(LoadError, match=f"no {part}"):
        _de._result(stored, "de", "leiden", CELLS)


def test_the_fingerprint_follows_the_settings_only():
    assert _result().params_hash == _result().params_hash
    other = _stored()
    other["params"] = {**other["params"], "corr_method": "bonferroni"}
    assert _de._result(other, "de", "leiden", CELLS).params_hash != _result().params_hash


def test_markers_are_the_top_up_regulated_significant_genes():
    a, b = _result().comparisons
    assert _de.markers(a, {"G1": "ABC1"}) == {
        "top": [{"gene": "G1", "log2fc": 3.0, "q": 1e-5, "pct_1": 0.9, "pct_2": 0.2,
                 "symbol": "ABC1"}],
        "n_significant": 1,
    }, "G2 has no fold change, so it isn't counted as up-regulated"
    assert _de.markers(b, {})["top"][0]["gene"] == "G3"


def test_markers_keep_at_most_the_top_25_by_fdr_then_fold_change():
    genes = [{"gene": f"g{i}", "log2fc": 1.0 + i, "pvalue": 0.001, "fdr": 0.001 * (i % 3),
              "pct_1": None, "pct_2": None} for i in range(40)]
    top = _de.markers(_de.Comparison("A", 2, 2, genes), {})["top"]
    assert len(top) == 25
    assert [(g["q"], -g["log2fc"]) for g in top] == sorted((g["q"], -g["log2fc"]) for g in top)


# --- reading it from a file ---------------------------------------------------


def _with_de(path, stored, symbols=None):
    from anndata.io import write_elem

    with h5py.File(path, "a") as f:
        write_elem(f["uns"], "de", stored)
        if symbols is not None:
            write_elem(f, "var", pd.DataFrame({"gene_symbols": symbols},
                                              index=[f"gene{j}" for j in range(len(symbols))]))
    return path


def test_the_result_is_read_from_the_files_uns(tmp_path):
    path = _with_de(write_h5ad(tmp_path / "d.h5ad"), _stored(), ["S0", "S1", "S2", "S3"])
    result = _de.read(path, "de", "leiden", CELLS)
    assert [c.cluster for c in result.comparisons] == CLUSTERS
    assert result.symbols == {"gene0": "S0", "gene1": "S1", "gene2": "S2", "gene3": "S3"}


def test_a_file_without_the_key_is_refused(tmp_path):
    with pytest.raises(LoadError, match=r"no uns\['de'\]"):
        _de.read(write_h5ad(tmp_path / "d.h5ad"), "de", "leiden", CELLS)


# --- loading it ---------------------------------------------------------------


def test_the_load_writes_the_run_comparisons_genes_and_statistics(tmp_path):
    client = _client_with_genes(1000)
    _load_with(client, tmp_path, _result())
    (run,) = client.tables["scrna_de_runs"]
    assert (run["source"], run["status"], run["method"]) == (
        "batch", "complete", "scanpy-wilcoxon-one-vs-rest")
    assert run["completed_at"] and run["params"]["reference"] == "rest"
    comparisons = {c["cluster_id"]: c for c in client.tables["scrna_de"]}
    assert set(comparisons) == {"A", "B"}
    a = comparisons["A"]
    assert {k: a[k] for k in ("contrast", "group1", "group2", "group_kind", "tested",
                              "n_genes_tested", "n_group1", "n_group2", "run_id")} == {
        "contrast": "A_vs_rest", "group1": "A", "group2": "rest", "group_kind": "cluster",
        "tested": True, "n_genes_tested": 2, "n_group1": 2, "n_group2": 2, "run_id": run["id"]}
    genes = [g for g in client.tables["scrna_de_genes"] if g["de_id"] == a["id"]]
    assert {g["gene_id"] for g in genes} == {50, 51}
    stats = {s["cluster_id"]: s for s in client.tables["scrna_cluster_stats"]}
    assert (stats["A"]["cell_count"], stats["A"]["pct"]) == (2, 50.0)
    assert (stats["A"]["centroid_x"], stats["A"]["centroid_y"]) == (1.0, 2.0)
    assert stats["A"]["markers"]["top"][0]["symbol"] == "ABC1"


def test_the_de_is_written_before_the_dataset_is_finished(tmp_path):
    client = _client_with_genes(1000)
    _load_with(client, tmp_path, _result())
    writes = [(op, table) for op, table, *_ in client.log if op != "select"]
    assert writes[-1] == ("update", "scrna_datasets"), "finishing is the last write"
    assert ("insert", "scrna_cluster_stats") in writes[:-1]


def test_a_cluster_without_tested_genes_is_stored_as_untested(tmp_path):
    stored = _stored()
    stored["pvals"] = _records({"A": [1e-6, 0.02, 0.5], "B": [np.nan] * 3}, float)
    client = _client_with_genes(1000)
    _load_with(client, tmp_path, _de._result(stored, "de", "leiden", CELLS))
    b = next(c for c in client.tables["scrna_de"] if c["cluster_id"] == "B")
    assert (b["tested"], b["n_genes_tested"]) == (False, 0)


def test_a_gene_the_dataset_doesnt_have_is_refused(tmp_path):
    client = _client_with_genes(1000)
    client.tables["scrna_genes"] = client.tables["scrna_genes"][:2]
    with pytest.raises(LoadError, match="doesn't have, e.g. 'G3'"):
        _load_with(client, tmp_path, _result())


def test_without_de_nothing_is_written(tmp_path):
    client = _client_with_genes(1000)
    _load_with(client, tmp_path, None)
    for table in ("scrna_de_runs", "scrna_de", "scrna_de_genes", "scrna_cluster_stats"):
        assert not client.tables.get(table), table


def test_a_resumed_load_continues_the_same_run_writing_only_what_is_missing(tmp_path):
    result = _result()
    client = _client_with_genes(1000)
    _load_with(client, tmp_path, result)
    counts = {t: len(client.tables[t]) for t in
              ("scrna_de_runs", "scrna_de", "scrna_de_genes", "scrna_cluster_stats")}
    # A load that stopped after some of B's genes, before B's statistics.
    b = next(c for c in client.tables["scrna_de"] if c["cluster_id"] == "B")
    client.tables["scrna_de_genes"] = [g for g in client.tables["scrna_de_genes"]
                                       if not (g["de_id"] == b["id"] and g["gene_id"] == 52)]
    client.tables["scrna_cluster_stats"] = [s for s in client.tables["scrna_cluster_stats"]
                                            if s["cluster_id"] == "A"]
    _de.write(_writer(client, tmp_path), 1000, result, CELLS)
    assert {t: len(client.tables[t]) for t in counts} == counts


# --- the resume check ---------------------------------------------------------


def _with_rows(**tables) -> FakeClient:
    return FakeClient({name: rows for name, rows in tables.items()})


def test_a_resume_with_de_keeps_its_own_runs_rows(tmp_path):
    client = _with_rows(scrna_de_runs=[{"id": 1, "dataset_id": 7, "params_hash": "h"}],
                        scrna_de=[{"dataset_id": 7, "run_id": 1}],
                        scrna_cluster_stats=[{"dataset_id": 7, "cluster_id": "A"}])
    check_nothing_later(_writer(client, tmp_path), 7, "h")


@pytest.mark.parametrize("tables, de_hash", [
    ({"scrna_de_runs": [{"id": 1, "dataset_id": 7, "params_hash": "other"}],
      "scrna_de": [{"dataset_id": 7, "run_id": 1}]}, "h"),
    ({"scrna_de": [{"dataset_id": 7, "run_id": None}]}, "h"),
    ({"scrna_cluster_neighbors": [{"dataset_id": 7}]}, "h"),
    ({"scrna_de": [{"dataset_id": 7, "run_id": 1}]}, None),
    ({"scrna_cluster_stats": [{"dataset_id": 7}]}, None),
], ids=["another-run", "a-file-row", "neighbours", "de-without-de-key", "stats-without-de-key"])
def test_a_resume_still_refuses_rows_it_didnt_write(tmp_path, tables, de_hash):
    with pytest.raises(LoadError, match="an admin has to look at it"):
        check_nothing_later(_writer(_with_rows(**tables), tmp_path), 7, de_hash)


# --- the command --------------------------------------------------------------


def _file_with_de(tmp_path):
    """The fixture's three cells are each their own cluster, cell0-cell2, over gene0-gene3."""
    groups = ["cell0", "cell1", "cell2"]
    genes = [f"gene{j}" for j in range(4)]

    def records(values, dtype):
        return np.rec.fromarrays([np.asarray(values, dtype=dtype)] * 3, names=groups)

    stored = {
        "params": {"groupby": "cell_type", "reference": "rest", "method": "wilcoxon"},
        "names": records(genes, object),
        "logfoldchanges": records([2.0, 1.0, 0.5, -1.0], float),
        "pvals": records([1e-5, 1e-3, 0.2, 0.5], float),
        "pvals_adj": records([1e-4, 1e-2, 0.4, 0.6], float),
    }
    return _with_de(write_h5ad(tmp_path / "d.h5ad"), stored)


def test_the_command_loads_the_de_and_says_so(tmp_path, env, storage):
    result = _run("upload", "--yes", "--de-key", "de", str(_file_with_de(tmp_path)))
    assert result.exit_code == 0, result.output
    assert "Differential expression: 3 clusters each against the rest" in result.output
    client = env["client"]
    assert len(client.tables["scrna_de"]) == 3
    assert len(client.tables["scrna_de_genes"]) == 12
    assert len(client.tables["scrna_cluster_stats"]) == 3


def test_a_dry_run_reads_and_shows_the_de_but_writes_nothing(tmp_path, env, storage):
    result = _run("upload", "--dry-run", "--de-key", "de", str(_file_with_de(tmp_path)))
    assert result.exit_code == 0, result.output
    assert "Differential expression: 3 clusters" in result.output
    assert not env["client"].tables.get("scrna_de_runs")


def test_a_missing_key_is_refused_before_anything_is_sent(tmp_path, env, storage):
    result = _run("upload", "--yes", "--de-key", "nope", str(write_h5ad(tmp_path / "d.h5ad")))
    assert result.exit_code != 0 and "Nothing was sent" in result.output
    assert not storage.objects


def test_de_key_isnt_taken_with_add_labels(tmp_path, env, storage):
    result = _run("upload", "--name", "X", "--species", "Arabidopsis", "--annotation",
                  "cell_type", "--add-labels", "--facet", "sample", "--de-key", "de",
                  str(write_h5ad(tmp_path / "d.h5ad")))
    assert result.exit_code == 2 and "--de-key" in result.output
