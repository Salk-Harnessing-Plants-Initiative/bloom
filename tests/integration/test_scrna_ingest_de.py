"""
Integration tests for the differential expression loader, `scripts/ingest_scrnaseq_de.py`,
writing through the site's API as a writer account. A load is one analysis: a batch
run, a row per comparison and a row per tested gene. Only the real database can say
whether those rows are legal, so these write them.

Each test loads cells with the cells loader and registers genes, which is what a DE
load needs. Writes commit, so each test works under a species of its own, removed with
everything under it afterwards.
"""

from __future__ import annotations

import math

import pytest

from tests.integration.scrna_loaders import load_script, losing, signer

KEY = ("Cortex", "pFACT_vs_Col-0")
GENES = ("AT1G00001", "AT1G00002")
PARAMS = {"summary_sha256": "s", "results_sha256": "r"}
OPTIONS = {"annotation": "ann", "sample_column": "sample", "umap_key": "X_umap",
           "source_column": None, "expression_units": "log1p normalised counts"}


@pytest.fixture(scope="module")
def cells_loader():
    return load_script("ingest_scrnaseq")


@pytest.fixture(scope="module")
def de(cells_loader):
    return load_script("ingest_scrnaseq_de")


@pytest.fixture
def sign_in(de, scrna_accounts, scrna_api, tmp_path):
    return signer(de.ingest_api, scrna_accounts, scrna_api, tmp_path / "marker.json")


@pytest.fixture
def db(pg_conninfo):
    import psycopg

    with psycopg.connect(pg_conninfo, autocommit=True) as conn:
        yield conn


def cells_table(cell_types) -> dict:
    labels = [cell_types[i % len(cell_types)] for i in range(max(2, len(cell_types)))]
    n = len(labels)
    return {"n_cells": n, "n_genes": 100, "x": [float(i) for i in range(n)],
            "y": [float(i) + 0.5 for i in range(n)], "labels": labels,
            "samples": ["Col-0"] * n, "levels": sorted(set(labels)),
            "barcodes": [f"BC{i}" for i in range(n)], "sources": {}}


def ready(cells_loader, sign_in, db, species_id, cell_types=("Cortex",), genes=GENES,
          name="de-set") -> int:
    """A dataset with finished cells of these types and these genes registered."""
    dataset_id, _, _ = cells_loader.load(sign_in(), name, species_id,
                                         cells_table(cell_types), "sha-1", OPTIONS,
                                         create=True)
    for number, gene in enumerate(genes):
        db.execute("INSERT INTO scrna_genes (dataset_id, gene_number, gene_name) "
                   "VALUES (%s, %s, %s)", (dataset_id, number, gene))
    return dataset_id


def entry(celltype="Cortex", contrast="pFACT_vs_Col-0", tested=True) -> dict:
    return {"celltype": celltype, "contrast": contrast, "group1": "pFACT",
            "group2": "Col-0", "n_group1": 10, "n_group2": 20, "tested": tested,
            "counts": None}


def rows(genes=GENES, log2fc=1.0) -> list[dict]:
    return [{"gene": g, "log2fc": log2fc, "pvalue": 0.001, "fdr": 0.01, "pct_1": 0.5,
             "pct_2": 0.25, "_fdr": True, "_fdr_lfc": True} for g in genes]


def load(de, writer, species_id, summary, groups, *, name="de-set", params_hash="hash-1",
         method="seurat-wilcoxon"):
    return de.load(writer, name, species_id, method, PARAMS, params_hash, summary, groups)


def stored(db, dataset_id) -> tuple:
    """Runs, comparisons and gene rows, without ids or timestamps."""
    return (
        db.execute("SELECT source, status, method, params, params_hash, requested_by, "
                   "completed_at IS NOT NULL FROM scrna_de_runs WHERE dataset_id = %s "
                   "ORDER BY id", (dataset_id,)).fetchall(),
        db.execute("SELECT cluster_id, contrast, group1, group2, n_group1, n_group2, "
                   "group_kind, method, params_hash, tested, n_genes_tested, file_path "
                   "FROM scrna_de WHERE dataset_id = %s ORDER BY cluster_id, contrast",
                   (dataset_id,)).fetchall(),
        db.execute("SELECT d.cluster_id, d.contrast, g.gene_name, x.log2fc, x.pvalue, "
                   "x.fdr, x.pct_1, x.pct_2 FROM scrna_de_genes x "
                   "JOIN scrna_de d ON d.id = x.de_id JOIN scrna_genes g ON g.id = x.gene_id "
                   "WHERE x.dataset_id = %s ORDER BY 1, 2, 3", (dataset_id,)).fetchall(),
    )


# --------------------------------------------------------------------------- #
# Writing an analysis
# --------------------------------------------------------------------------- #


def test_a_load_is_one_complete_batch_analysis_the_writer_can_read_back(
    de, cells_loader, sign_in, db, scrna_species, scrna_accounts
):
    dataset_id = ready(cells_loader, sign_in, db, scrna_species)
    writer = sign_in()
    run_id, comparisons, genes, outcome = load(
        de, writer, scrna_species, [entry(), entry(contrast="x_vs_y", tested=False)],
        {KEY: rows()})
    assert (comparisons, genes, outcome) == (2, 2, "loaded")

    runs, results, gene_rows = stored(db, dataset_id)
    (source, status, method, params, params_hash, requested_by, completed) = runs[0]
    assert (source, status, method, params, params_hash, completed) == (
        "batch", "complete", "seurat-wilcoxon", PARAMS, "hash-1", True)
    assert str(requested_by) == scrna_accounts["writer"]["id"]
    assert results == [
        ("Cortex", "pFACT_vs_Col-0", "pFACT", "Col-0", 10, 20, "genotype",
         "seurat-wilcoxon", "hash-1", True, 2, None),
        ("Cortex", "x_vs_y", "pFACT", "Col-0", 10, 20, "genotype",
         "seurat-wilcoxon", "hash-1", False, 0, None),
    ]
    assert gene_rows == [("Cortex", "pFACT_vs_Col-0", g, 1.0, 0.001, 0.01, 0.5, 0.25)
                         for g in GENES]

    client = writer.session.client
    assert [r["id"] for r in client.table("scrna_de_runs").select("id")
            .eq("dataset_id", dataset_id).execute().data] == [run_id]
    assert len(client.table("scrna_de_genes").select("id")
               .eq("dataset_id", dataset_id).execute().data) == 2


@pytest.mark.parametrize("log2fc", [None, math.inf, -math.inf])
def test_a_fold_change_is_stored_as_read(de, cells_loader, sign_in, db, scrna_species,
                                         log2fc):
    """No fold change is NULL, and an unbounded one stays infinite."""
    dataset_id = ready(cells_loader, sign_in, db, scrna_species)
    load(de, sign_in(), scrna_species, [entry()], {KEY: rows(log2fc=log2fc)})
    assert db.execute("SELECT DISTINCT log2fc FROM scrna_de_genes WHERE dataset_id = %s",
                      (dataset_id,)).fetchall() == [(log2fc,)]


def test_every_comparison_of_the_real_shape_is_legal(de, cells_loader, sign_in, db,
                                                     scrna_species):
    """69 comparisons over 23 cell types and 3 contrasts, 46 tested: the shape of the
    real export, written against the real constraints."""
    cell_types = [f"Type{i}" for i in range(23)]
    contrasts = ["pFACT_vs_Col-0", "pFACT_vs_pHORST", "pHORST_vs_Col-0"]
    dataset_id = ready(cells_loader, sign_in, db, scrna_species, cell_types)
    summary, groups = [], {}
    for i, cell_type in enumerate(cell_types):
        for j, contrast in enumerate(contrasts):
            tested = (i * 3 + j) < 46
            summary.append(entry(cell_type, contrast, tested))
            if tested:
                groups[(cell_type, contrast)] = rows()
    _, comparisons, genes, _ = load(de, sign_in(), scrna_species, summary, groups)
    assert (comparisons, genes) == (69, 92)
    assert db.execute("SELECT count(*) FILTER (WHERE tested), count(*) FILTER "
                      "(WHERE NOT tested) FROM scrna_de WHERE dataset_id = %s",
                      (dataset_id,)).fetchone() == (46, 23)


def test_a_different_fingerprint_adds_an_analysis_beside_the_first(
    de, cells_loader, sign_in, db, scrna_species
):
    dataset_id = ready(cells_loader, sign_in, db, scrna_species)
    first, _, _, _ = load(de, sign_in(), scrna_species, [entry()], {KEY: rows()})
    second, _, _, _ = load(de, sign_in(), scrna_species, [entry()], {KEY: rows()},
                           params_hash="hash-2")
    assert db.execute("SELECT run_id, count(*) FROM scrna_de WHERE dataset_id = %s "
                      "GROUP BY run_id ORDER BY run_id", (dataset_id,)).fetchall() == [
        (first, 1), (second, 1)]


def test_one_dataset_s_load_does_not_touch_another_s(de, cells_loader, sign_in, db,
                                                     scrna_species):
    keep = ready(cells_loader, sign_in, db, scrna_species, name="keep")
    ready(cells_loader, sign_in, db, scrna_species, name="other")
    load(de, sign_in(), scrna_species, [entry()], {KEY: rows()}, name="keep")
    load(de, sign_in(), scrna_species, [entry()], {KEY: rows()}, name="other")
    assert db.execute("SELECT count(*) FROM scrna_de WHERE dataset_id = %s",
                      (keep,)).fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM scrna_de_genes WHERE dataset_id = %s",
                      (keep,)).fetchone()[0] == 2


def test_an_admin_account_can_load(de, cells_loader, sign_in, db, scrna_species):
    ready(cells_loader, sign_in, db, scrna_species)
    assert load(de, sign_in("admin"), scrna_species, [entry()], {KEY: rows()})[3] == "loaded"


# --------------------------------------------------------------------------- #
# Resuming
# --------------------------------------------------------------------------- #


BOTH = [entry(), entry("Xylem")]
BOTH_ROWS = {KEY: rows(), ("Xylem", "pFACT_vs_Col-0"): rows()}


def test_a_stopped_load_resumes_to_the_same_rows(de, cells_loader, sign_in, db,
                                                 scrna_species, monkeypatch):
    monkeypatch.setattr(de, "GENE_BATCH", 1)
    whole = ready(cells_loader, sign_in, db, scrna_species, ("Cortex", "Xylem"),
                  name="whole")
    load(de, sign_in(), scrna_species, BOTH, BOTH_ROWS, name="whole")

    part = ready(cells_loader, sign_in, db, scrna_species, ("Cortex", "Xylem"),
                 name="part")
    with pytest.raises(de.IngestError, match="re-run the same command"):
        load(de, sign_in(create_client=losing("scrna_de_genes", 2)), scrna_species, BOTH,
             BOTH_ROWS, name="part")
    assert len(stored(db, part)[2]) == 2, "the lost batch had committed"

    _, comparisons, genes, outcome = load(de, sign_in(), scrna_species, BOTH, BOTH_ROWS,
                                          name="part")
    assert (comparisons, genes, outcome) == (0, 2, "resumed")
    assert stored(db, part) == stored(db, whole)


def test_a_lost_reply_to_the_comparisons_resumes_without_a_second_copy(
    de, cells_loader, sign_in, db, scrna_species
):
    dataset_id = ready(cells_loader, sign_in, db, scrna_species, ("Cortex", "Xylem"))
    with pytest.raises(de.IngestError, match="outcome is unknown"):
        load(de, sign_in(create_client=losing("scrna_de")), scrna_species, BOTH, BOTH_ROWS)
    assert load(de, sign_in(), scrna_species, BOTH, BOTH_ROWS)[1:] == (0, 4, "resumed")
    assert len(stored(db, dataset_id)[1]) == 2


def test_the_same_files_after_a_full_load_are_already_loaded(de, cells_loader, sign_in,
                                                             db, scrna_species):
    dataset_id = ready(cells_loader, sign_in, db, scrna_species)
    run_id, _, _, _ = load(de, sign_in(), scrna_species, [entry()], {KEY: rows()})
    before = stored(db, dataset_id)
    assert load(de, sign_in(), scrna_species, [entry()], {KEY: rows()}) == (
        run_id, 0, 0, "already loaded")
    assert stored(db, dataset_id) == before


def test_two_batch_runs_with_one_fingerprint_are_refused_naming_both(
    de, cells_loader, sign_in, db, scrna_species
):
    dataset_id = ready(cells_loader, sign_in, db, scrna_species)
    run_id, _, _, _ = load(de, sign_in(), scrna_species, [entry()], {KEY: rows()})
    (other,) = db.execute(
        "INSERT INTO scrna_de_runs (dataset_id, source, status, method, params_hash, "
        "completed_at) VALUES (%s, 'batch', 'complete', 'seurat-wilcoxon', 'hash-1', "
        "now()) RETURNING id", (dataset_id,)).fetchone()
    with pytest.raises(de.IngestError, match=f"{run_id}, {other}"):
        load(de, sign_in(), scrna_species, [entry()], {KEY: rows()})


# --------------------------------------------------------------------------- #
# Refusals, before anything is written
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("cell_types,genes,summary,named", [
    (("Cortex",), (), [entry()], "Load its gene counts first"),
    (("Cortex", "Xylem"), GENES, [entry("Phellem")], "not in the catalogue: Phellem"),
    (("Cortex",), ("AT1G00001",), [entry()], "not registered for this dataset: AT1G00002"),
    (("Cortex",), (*GENES, "AT1G00001"), [entry()], "registered more than once"),
])
def test_a_dataset_not_ready_for_these_results_is_refused(
    de, cells_loader, sign_in, db, scrna_species, cell_types, genes, summary, named
):
    dataset_id = ready(cells_loader, sign_in, db, scrna_species, cell_types, genes)
    with pytest.raises(de.IngestError, match=named):
        load(de, sign_in(), scrna_species, summary, {KEY: rows()})
    assert stored(db, dataset_id) == ([], [], [])


def test_an_unfinished_dataset_is_refused(de, cells_loader, sign_in, db, scrna_species):
    with pytest.raises(de.IngestError, match="outcome is unknown"):
        cells_loader.load(sign_in(create_client=losing("scrna_cells")), "de-set",
                          scrna_species, cells_table(("Cortex",)), "sha-1", OPTIONS,
                          create=True)
    with pytest.raises(de.IngestError, match="not finished"):
        load(de, sign_in(), scrna_species, [entry()], {KEY: rows()})
