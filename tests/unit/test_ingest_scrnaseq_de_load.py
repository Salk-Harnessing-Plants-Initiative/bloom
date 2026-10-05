"""
Unit tests for the write path of `scripts/ingest_scrnaseq_de.py`, against an in-memory
client: one analysis per pair of files, resumed by its fingerprint, writing only what
is missing. The same flows run through the real API in
tests/integration/test_scrna_ingest_de.py.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

import httpx
import pytest

from tests.unit.fake_supabase import FakeClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "ingest_scrnaseq_de.py"

CORTEX = ("Cortex", "pFACT_vs_Col-0")
XYLEM = ("Xylem", "pFACT_vs_Col-0")
GENES = {"AT1G00001": 11, "AT1G00002": 12, "AT1G00003": 13}
PARAMS = {"summary_sha256": "s", "results_sha256": "r"}


@pytest.fixture(scope="module")
def de():
    spec = importlib.util.spec_from_file_location("ingest_scrnaseq_de", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["ingest_scrnaseq_de"] = module
    spec.loader.exec_module(module)
    return module


def entry(key=CORTEX, tested=True) -> dict:
    return {"celltype": key[0], "contrast": key[1], "group1": "pFACT", "group2": "Col-0",
            "n_group1": 10, "n_group2": 20, "tested": tested, "counts": None}


def rows(genes=tuple(GENES), log2fc=1.0) -> list[dict]:
    return [{"gene": g, "log2fc": log2fc, "pvalue": 0.001, "fdr": 0.01, "pct_1": 0.5,
             "pct_2": 0.25, "_fdr": True, "_fdr_lfc": True} for g in genes]


SUMMARY = [entry(CORTEX), entry(XYLEM, tested=False)]
GROUPS = {CORTEX: rows()}


def client_with(*, ingested=True, cell_types=("Cortex", "Xylem"), genes=GENES,
                **tables) -> FakeClient:
    return FakeClient({
        "scrna_datasets": [{"id": 7, "name": "MYB41", "species_id": 1, "deleted_at": None,
                            "source_checksum": "sha", "metadata": {}, "n_cells": 30,
                            "ingested_at": "2026-09-11T00:00:00+00:00" if ingested else None}],
        "scrna_clusters": [{"dataset_id": 7, "cluster_id": c} for c in cell_types],
        "scrna_genes": [{"id": i, "dataset_id": 7, "gene_name": g} for g, i in genes.items()],
        **tables,
    })


def writer(de, client, tmp_path):
    api = de.ingest_api
    session = api.Session(lambda: (client, "bloom_writer", "u1"), client, "bloom_writer", "u1")
    return api.Writer(session, api.Marker(tmp_path / "m.json", wait_s=0))


def load(de, client, tmp_path, summary=SUMMARY, groups=GROUPS, method="seurat-wilcoxon",
         params_hash="hash-1"):
    return de.load(writer(de, client, tmp_path), "MYB41", 1, method, PARAMS, params_hash,
                   summary, groups)


def writes(client, since=0):
    return [e for e in client.log[since:] if e[0] != "select"]


def snapshot(client) -> tuple:
    """Runs, comparisons and gene rows without ids or timestamps."""
    runs = [{k: v for k, v in r.items() if k not in ("id", "completed_at")}
            for r in client.tables["scrna_de_runs"]]
    key_of = {r["id"]: (r["cluster_id"], r["contrast"]) for r in client.tables["scrna_de"]}
    comparisons = sorted(sorted((k, repr(v)) for k, v in r.items() if k not in ("id", "run_id"))
                         for r in client.tables["scrna_de"])
    genes = sorted((key_of[g["de_id"]], g["gene_id"], repr(g["log2fc"]), g["pvalue"])
                   for g in client.tables.get("scrna_de_genes", []))
    return runs, comparisons, genes


# --------------------------------------------------------------------------- #
# A first load
# --------------------------------------------------------------------------- #


def test_a_first_load_records_one_complete_batch_analysis(de, tmp_path):
    client = client_with()
    run_id, comparisons, genes, outcome = load(de, client, tmp_path)
    assert (comparisons, genes, outcome) == (2, 3, "loaded")
    (run,) = client.tables["scrna_de_runs"]
    assert run["id"] == run_id
    assert {k: run[k] for k in ("dataset_id", "source", "status", "method", "params",
                                "params_hash", "requested_by")} == {
        "dataset_id": 7, "source": "batch", "status": "complete",
        "method": "seurat-wilcoxon", "params": PARAMS, "params_hash": "hash-1",
        "requested_by": "u1"}
    assert run["completed_at"]


def test_every_comparison_carries_its_gene_count(de, tmp_path):
    """0 for a skipped comparison, which the schema requires of a run's rows."""
    client = client_with()
    run_id, _, _, _ = load(de, client, tmp_path)
    got = {(r["cluster_id"], r["contrast"]): r for r in client.tables["scrna_de"]}
    assert (got[CORTEX]["tested"], got[CORTEX]["n_genes_tested"]) == (True, 3)
    assert (got[XYLEM]["tested"], got[XYLEM]["n_genes_tested"]) == (False, 0)
    assert {k: got[CORTEX][k] for k in ("dataset_id", "run_id", "group1", "group2",
                                        "n_group1", "n_group2", "group_kind", "method",
                                        "params_hash")} == {
        "dataset_id": 7, "run_id": run_id, "group1": "pFACT", "group2": "Col-0",
        "n_group1": 10, "n_group2": 20, "group_kind": "genotype",
        "method": "seurat-wilcoxon", "params_hash": "hash-1"}


def test_gene_rows_name_their_comparison_and_gene(de, tmp_path):
    client = client_with()
    load(de, client, tmp_path)
    (cortex,) = [r["id"] for r in client.tables["scrna_de"] if r["cluster_id"] == "Cortex"]
    assert sorted((g["de_id"], g["dataset_id"], g["gene_id"], g["log2fc"], g["pvalue"],
                   g["fdr"], g["pct_1"], g["pct_2"])
                  for g in client.tables["scrna_de_genes"]) == [
        (cortex, 7, i, 1.0, 0.001, 0.01, 0.5, 0.25) for i in (11, 12, 13)]


def test_an_infinite_fold_change_is_sent_as_text_json_can_carry(de, tmp_path):
    """JSON has no spelling for infinity; Postgres reads the text as the number."""
    client = client_with()
    groups = {CORTEX: [*rows(["AT1G00001"], math.inf), *rows(["AT1G00002"], -math.inf),
                       *rows(["AT1G00003"], None)]}
    load(de, client, tmp_path, groups=groups)
    by_gene = {g["gene_id"]: g["log2fc"] for g in client.tables["scrna_de_genes"]}
    assert by_gene == {11: "Infinity", 12: "-Infinity", 13: None}
    json.dumps(client.tables["scrna_de_genes"], allow_nan=False)


def test_every_insert_goes_out_with_retries_off(de, tmp_path):
    client = client_with()
    load(de, client, tmp_path)
    inserts = [e for e in client.log if e[0] == "insert"]
    assert inserts and all(e[3] is False for e in inserts)


# --------------------------------------------------------------------------- #
# Resuming
# --------------------------------------------------------------------------- #


def test_the_plan_writes_only_what_is_missing(de):
    new, genes = de.plan(SUMMARY, GROUPS, {CORTEX: 50}, {50: {11}}, GENES)
    assert [(e["celltype"], e["contrast"]) for e in new] == [XYLEM]
    assert {k: [r["gene"] for r in v] for k, v in genes.items()} == {
        CORTEX: ["AT1G00002", "AT1G00003"]}


def test_a_comparison_holding_some_of_its_rows_gets_the_rest(de, tmp_path):
    client = client_with(
        scrna_de_runs=[{"id": 5, "dataset_id": 7, "source": "batch", "method": "seurat-wilcoxon",
                        "params_hash": "hash-1"}],
        scrna_de=[{"id": 50, "dataset_id": 7, "run_id": 5, "cluster_id": "Cortex",
                   "contrast": "pFACT_vs_Col-0"}],
        scrna_de_genes=[{"id": 1, "de_id": 50, "dataset_id": 7, "gene_id": 11}])
    assert load(de, client, tmp_path) == (5, 1, 2, "resumed")
    assert len(client.tables["scrna_de_runs"]) == 1
    assert sorted((r["cluster_id"], r["run_id"]) for r in client.tables["scrna_de"]) == [
        ("Cortex", 5), ("Xylem", 5)]
    assert sorted(g["gene_id"] for g in client.tables["scrna_de_genes"]
                  if g["de_id"] == 50) == [11, 12, 13]


def test_a_stopped_load_resumes_to_the_same_rows(de, tmp_path, monkeypatch):
    monkeypatch.setattr(de, "GENE_BATCH", 1)
    whole = client_with()
    load(de, whole, tmp_path)

    client = client_with()
    seen = []
    def second_gene_batch(op, table, payload):
        if op == "insert" and table == "scrna_de_genes":
            seen.append(1)
            return len(seen) == 2
        return False
    client.fail(second_gene_batch, httpx.ReadTimeout("lost"), commit=True)
    with pytest.raises(de.IngestError, match="re-run the same command"):
        load(de, client, tmp_path)
    assert len(client.tables["scrna_de_genes"]) == 2

    _, comparisons, genes, outcome = load(de, client, tmp_path)
    assert (comparisons, genes, outcome) == (0, 1, "resumed")
    assert snapshot(client) == snapshot(whole)


def test_the_same_files_after_a_full_load_are_already_loaded(de, tmp_path):
    client = client_with()
    run_id, _, _, _ = load(de, client, tmp_path)
    before = len(client.log)
    assert load(de, client, tmp_path) == (run_id, 0, 0, "already loaded")
    assert writes(client, before) == []


def test_two_batch_runs_with_one_fingerprint_are_refused_naming_both(de, tmp_path):
    runs = [{"id": i, "dataset_id": 7, "source": "batch", "method": "seurat-wilcoxon",
             "params_hash": "hash-1"} for i in (5, 6)]
    client = client_with(scrna_de_runs=runs)
    with pytest.raises(de.IngestError, match="5, 6"):
        load(de, client, tmp_path)
    assert writes(client) == []


def test_a_found_run_with_another_method_is_refused_naming_both(de, tmp_path):
    client = client_with(scrna_de_runs=[{"id": 5, "dataset_id": 7, "source": "batch",
                                         "method": "edger", "params_hash": "hash-1"}])
    with pytest.raises(de.IngestError, match="edger.*seurat-wilcoxon"):
        load(de, client, tmp_path)
    assert writes(client) == []


def test_an_on_demand_run_with_the_same_fingerprint_is_left_alone(de, tmp_path):
    client = client_with(scrna_de_runs=[{"id": 5, "dataset_id": 7, "source": "ondemand",
                                         "method": "seurat-wilcoxon", "params_hash": "hash-1"}])
    run_id, _, _, outcome = load(de, client, tmp_path)
    assert run_id != 5 and outcome == "loaded"


# --------------------------------------------------------------------------- #
# Refusals, before anything is written
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kwargs,summary,named", [
    ({"ingested": False}, SUMMARY, "not finished"),
    ({"cell_types": ()}, SUMMARY, "Load its cells first"),
    ({}, [entry(("Phellem", "pFACT_vs_Col-0"))], "not in the catalogue: Phellem"),
    ({"genes": {}}, SUMMARY, "Load its gene counts first"),
    ({"genes": {"AT1G00001": 11, "AT1G00002": 12}}, SUMMARY,
     "not registered for this dataset: AT1G00003"),
])
def test_a_dataset_not_ready_for_these_results_is_refused(de, tmp_path, kwargs, summary,
                                                          named):
    client = client_with(**kwargs)
    with pytest.raises(de.IngestError, match=named):
        load(de, client, tmp_path, summary=summary)
    assert writes(client) == []


def test_a_gene_registered_twice_cannot_be_resolved(de, tmp_path):
    client = client_with()
    client.tables["scrna_genes"].append({"id": 99, "dataset_id": 7, "gene_name": "AT1G00002"})
    with pytest.raises(de.IngestError, match="registered more than once"):
        load(de, client, tmp_path)
    assert writes(client) == []


def test_an_unknown_dataset_says_to_load_the_cells_first(de, tmp_path):
    client = client_with()
    with pytest.raises(de.IngestError, match="Load its cells first"):
        de.load(writer(de, client, tmp_path), "other", 1, "seurat-wilcoxon", PARAMS,
                "hash-1", SUMMARY, GROUPS)
