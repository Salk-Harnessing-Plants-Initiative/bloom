"""
Integration tests for the database side of `scripts/ingest_scrnaseq_de.py`.

A load is one analysis: a run, a row per comparison and a row per tested gene.
Only a real database can say whether those rows are legal, so these write them.

LOCAL ONLY: every test rolls back.
"""

from __future__ import annotations

import importlib.util
import math
import sys
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "ingest_scrnaseq_de.py"

KEY = ("Cortex", "pFACT_vs_Col-0")
GENES = ("AT1G00001", "AT1G00002")
PARAMS = {"summary_sha256": "s", "results_sha256": "r"}


@pytest.fixture(scope="module")
def de():
    spec = importlib.util.spec_from_file_location("ingest_scrnaseq_de", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["ingest_scrnaseq_de"] = module
    spec.loader.exec_module(module)
    return module


def species(cur) -> int:
    tag = uuid.uuid4().hex[:10]
    cur.execute(
        "INSERT INTO species (common_name, genus, species) VALUES (%s, %s, %s) "
        "RETURNING id",
        (f"de-{tag}", f"Deus-{tag}", f"testis-{tag}"),
    )
    return cur.fetchone()[0]


def dataset(cur, species_id: int, name: str = "de-set") -> int:
    cur.execute(
        "INSERT INTO scrna_datasets (name, species_id) VALUES (%s, %s) "
        "RETURNING id", (name, species_id),
    )
    return cur.fetchone()[0]


def loaded_dataset(cur, cell_types=("Cortex",), genes=GENES,
                   name: str = "de-set") -> int:
    """A dataset whose cells and gene counts are loaded: what a DE load needs."""
    did = dataset(cur, species(cur), name)
    cur.executemany(
        "INSERT INTO scrna_clusters (dataset_id, cluster_id, ordinal, name, color) "
        "VALUES (%s, %s, %s, %s, '#000000')",
        [(did, t, i, t) for i, t in enumerate(cell_types)],
    )
    if genes:
        cur.executemany(
            "INSERT INTO scrna_genes (dataset_id, gene_number, gene_name) "
            "VALUES (%s, %s, %s)",
            [(did, i, g) for i, g in enumerate(genes)],
        )
    return did


def entry(celltype="Cortex", contrast="pFACT_vs_Col-0", tested=True) -> dict:
    return {"celltype": celltype, "contrast": contrast, "group1": "pFACT",
            "group2": "Col-0", "n_group1": 10, "n_group2": 20,
            "tested": tested, "counts": None}


def rows(genes=GENES, log2fc=1.0) -> list[dict]:
    return [{"gene": g, "log2fc": log2fc, "pvalue": 0.001, "fdr": 0.01,
             "pct_1": 0.5, "pct_2": 0.25, "_fdr": True, "_fdr_lfc": True}
            for g in genes]


def load(de, conn, did, summary, groups, params_hash=None):
    """Resolve the genes and write, as main() does once the checks pass."""
    ids = de.gene_ids(conn, did, groups)
    return de.write_de(conn, did, "seurat-wilcoxon", PARAMS,
                       params_hash or uuid.uuid4().hex, summary, groups, ids)


def scalar(cur, sql: str, *args):
    cur.execute(sql, args)
    return cur.fetchone()[0]


# --------------------------------------------------------------------------- #
# Finding the dataset, its cell types and its genes
# --------------------------------------------------------------------------- #


def test_an_unloaded_dataset_says_to_load_the_cells_first(de, pg_conn):
    with pg_conn.cursor() as cur:
        sid = species(cur)
    with pytest.raises(de.IngestError, match="Load its cells first"):
        de.open_dataset(pg_conn, "absent", sid)
    pg_conn.rollback()


def test_a_cell_type_the_catalogue_does_not_have_is_refused(de, pg_conn):
    with pg_conn.cursor() as cur:
        did = loaded_dataset(cur, ("Cortex", "Xylem"))
    with pytest.raises(de.IngestError, match="not in the catalogue: Phellem"):
        de.check_cell_types(pg_conn, did, [entry(celltype="Phellem")])
    pg_conn.rollback()


def test_an_empty_catalogue_says_to_load_the_cells_first(de, pg_conn):
    with pg_conn.cursor() as cur:
        did = dataset(cur, species(cur))
    with pytest.raises(de.IngestError, match="Load its cells first"):
        de.check_cell_types(pg_conn, did, [entry()])
    pg_conn.rollback()


def test_cell_types_that_all_exist_are_accepted(de, pg_conn):
    with pg_conn.cursor() as cur:
        did = loaded_dataset(cur, ("Cortex", "Xylem"))
    de.check_cell_types(pg_conn, did, [entry(), entry(celltype="Xylem")])
    pg_conn.rollback()


def test_no_registered_genes_says_to_load_the_counts_first(de, pg_conn):
    """Gene rows reference the catalogue, so without it they point at nothing."""
    with pg_conn.cursor() as cur:
        did = loaded_dataset(cur, genes=())
    with pytest.raises(de.IngestError, match="Load its gene counts first"):
        de.gene_ids(pg_conn, did, {KEY: rows()})
    pg_conn.rollback()


def test_a_gene_the_dataset_does_not_have_is_refused(de, pg_conn):
    with pg_conn.cursor() as cur:
        did = loaded_dataset(cur, genes=("AT1G00001",))
    with pytest.raises(de.IngestError,
                       match="not registered for this dataset: AT1G00002"):
        de.gene_ids(pg_conn, did, {KEY: rows()})
    pg_conn.rollback()


def test_a_gene_registered_twice_cannot_be_resolved(de, pg_conn):
    """Nothing makes a name unique within a dataset, so picking one would be a
    guess about which gene a result means."""
    with pg_conn.cursor() as cur:
        did = loaded_dataset(cur, genes=("AT1G00001", "AT1G00002", "AT1G00001"))
    with pytest.raises(de.IngestError, match="registered more than once"):
        de.gene_ids(pg_conn, did, {KEY: rows()})
    pg_conn.rollback()


def test_each_gene_resolves_to_its_own_catalogue_row(de, pg_conn):
    with pg_conn.cursor() as cur:
        did = loaded_dataset(cur)
        cur.execute("SELECT gene_name, id FROM scrna_genes WHERE dataset_id = %s",
                    (did,))
        expected = dict(cur.fetchall())
    assert de.gene_ids(pg_conn, did, {KEY: rows()}) == expected
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Writing an analysis
# --------------------------------------------------------------------------- #


def test_a_load_is_one_completed_batch_analysis(de, pg_conn):
    with pg_conn.cursor() as cur:
        did = loaded_dataset(cur)
    run_id, results, genes = load(de, pg_conn, did, [entry()], {KEY: rows()},
                                  "hash-1")
    assert (results, genes) == (1, 2)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT dataset_id, source, status, method, params, params_hash, "
            "completed_at IS NOT NULL FROM scrna_de_runs WHERE id = %s",
            (run_id,),
        )
        assert cur.fetchone() == (did, "batch", "complete", "seurat-wilcoxon",
                                  PARAMS, "hash-1", True)
    pg_conn.rollback()


def test_a_tested_comparison_is_written_with_its_genes(de, pg_conn):
    with pg_conn.cursor() as cur:
        did = loaded_dataset(cur)
    run_id, _, _ = load(de, pg_conn, did, [entry()], {KEY: rows()}, "hash-1")
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT id, run_id, cluster_id, contrast, group1, group2, n_group1, "
            "n_group2, group_kind, method, params_hash, tested, file_path "
            "FROM scrna_de WHERE dataset_id = %s", (did,),
        )
        (de_id, *row), = cur.fetchall()
        assert row == [run_id, "Cortex", "pFACT_vs_Col-0", "pFACT", "Col-0", 10,
                       20, "genotype", "seurat-wilcoxon", "hash-1", True, None]
        cur.execute(
            "SELECT g.gene_name, d.log2fc, d.pvalue, d.fdr, d.pct_1, d.pct_2 "
            "FROM scrna_de_genes d JOIN scrna_genes g ON g.id = d.gene_id "
            "WHERE d.de_id = %s ORDER BY g.gene_name", (de_id,),
        )
        assert cur.fetchall() == [(g, 1.0, 0.001, 0.01, 0.5, 0.25) for g in GENES]
    pg_conn.rollback()


def test_a_skipped_comparison_is_a_row_marked_untested_with_no_genes(de, pg_conn):
    """The group sizes are what explain why it was skipped."""
    with pg_conn.cursor() as cur:
        did = loaded_dataset(cur)
    load(de, pg_conn, did, [entry(tested=False)], {})
    with pg_conn.cursor() as cur:
        cur.execute("SELECT id, tested, n_group1, n_group2, file_path "
                    "FROM scrna_de WHERE dataset_id = %s", (did,))
        de_id, *row = cur.fetchone()
        assert row == [False, 10, 20, None]
        assert scalar(cur, "SELECT count(*) FROM scrna_de_genes WHERE de_id = %s",
                      de_id) == 0
    pg_conn.rollback()


@pytest.mark.parametrize("log2fc", [None, math.inf, -math.inf])
def test_a_fold_change_is_stored_as_read(de, pg_conn, log2fc):
    """No fold change is NULL, and an unbounded one stays infinite."""
    with pg_conn.cursor() as cur:
        did = loaded_dataset(cur)
    load(de, pg_conn, did, [entry()], {KEY: rows(log2fc=log2fc)})
    with pg_conn.cursor() as cur:
        cur.execute("SELECT DISTINCT log2fc FROM scrna_de_genes "
                    "WHERE dataset_id = %s", (did,))
        assert cur.fetchall() == [(log2fc,)]
    pg_conn.rollback()


def test_loading_again_adds_an_analysis_beside_the_first(de, pg_conn):
    """Nothing is replaced: the earlier analysis and its genes stay as they were."""
    with pg_conn.cursor() as cur:
        did = loaded_dataset(cur)
    first, _, _ = load(de, pg_conn, did, [entry()], {KEY: rows()}, "hash-1")
    second, _, _ = load(de, pg_conn, did, [entry()], {KEY: rows()}, "hash-2")
    with pg_conn.cursor() as cur:
        cur.execute("SELECT run_id, count(*) FROM scrna_de WHERE dataset_id = %s "
                    "GROUP BY run_id ORDER BY run_id", (did,))
        assert cur.fetchall() == [(first, 1), (second, 1)]
        assert scalar(cur, "SELECT count(*) FROM scrna_de_genes "
                           "WHERE dataset_id = %s", did) == 4
    pg_conn.rollback()


def test_the_same_files_cannot_be_loaded_twice(de, pg_conn):
    with pg_conn.cursor() as cur:
        did = loaded_dataset(cur)
    run_id, _, _ = load(de, pg_conn, did, [entry()], {KEY: rows()}, "hash-1")
    with pytest.raises(de.IngestError,
                       match=f"already loaded for dataset {did} as analysis "
                             f"{run_id}"):
        de.check_not_loaded(pg_conn, did, "hash-1")
    de.check_not_loaded(pg_conn, did, "hash-2")
    pg_conn.rollback()


def test_one_dataset_s_load_does_not_touch_another_s(de, pg_conn):
    with pg_conn.cursor() as cur:
        keep = loaded_dataset(cur, name="keep")
        other = loaded_dataset(cur, name="other")
    load(de, pg_conn, keep, [entry()], {KEY: rows()})
    load(de, pg_conn, other, [entry()], {KEY: rows()})
    with pg_conn.cursor() as cur:
        assert scalar(cur, "SELECT count(*) FROM scrna_de WHERE dataset_id = %s",
                      keep) == 1
        assert scalar(cur, "SELECT count(*) FROM scrna_de_genes "
                           "WHERE dataset_id = %s", keep) == 2
    pg_conn.rollback()


def test_every_comparison_of_the_real_shape_is_legal(de, pg_conn):
    """69 comparisons over 23 cell types and 3 contrasts, 46 tested -- the shape
    of the real export, written against the real constraints."""
    cell_types = [f"Type{i}" for i in range(23)]
    contrasts = ["pFACT_vs_Col-0", "pFACT_vs_pHORST", "pHORST_vs_Col-0"]
    with pg_conn.cursor() as cur:
        did = loaded_dataset(cur, cell_types)
    summary, groups = [], {}
    for i, t in enumerate(cell_types):
        for j, c in enumerate(contrasts):
            tested = (i * 3 + j) < 46
            summary.append(entry(celltype=t, contrast=c, tested=tested))
            if tested:
                groups[(t, c)] = rows()
    _, results, genes = load(de, pg_conn, did, summary, groups)
    assert (results, genes) == (69, 92)
    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FILTER (WHERE tested), "
                    "count(*) FILTER (WHERE NOT tested) "
                    "FROM scrna_de WHERE dataset_id = %s", (did,))
        assert cur.fetchone() == (46, 23)
    pg_conn.rollback()


def test_a_refusal_partway_leaves_nothing_behind(de, pg_conn):
    """The run, and every row written before the one refused, go with it."""
    with pg_conn.cursor() as cur:
        did = loaded_dataset(cur, ("Cortex", "Xylem"))
        other = loaded_dataset(cur, name="other")
        foreign = scalar(cur, "SELECT id FROM scrna_genes WHERE dataset_id = %s "
                              "LIMIT 1", other)
        cur.execute("SAVEPOINT before_load")
    ids = {**de.gene_ids(pg_conn, did, {KEY: rows()}), "AT9G99999": foreign}
    groups = {KEY: rows(), ("Xylem", "pFACT_vs_Col-0"): rows(genes=("AT9G99999",))}
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        de.write_de(pg_conn, did, "seurat-wilcoxon", PARAMS, "hash-1",
                    [entry(), entry(celltype="Xylem")], groups, ids)
    with pg_conn.cursor() as cur:
        cur.execute("ROLLBACK TO SAVEPOINT before_load")
        assert scalar(cur, "SELECT count(*) FROM scrna_de_runs "
                           "WHERE dataset_id = %s", did) == 0
        assert scalar(cur, "SELECT count(*) FROM scrna_de WHERE dataset_id = %s",
                      did) == 0
    pg_conn.rollback()


def test_the_ingest_role_can_write_an_analysis(de, pg_conn):
    """bloom_writer is the ingest role, so every read and insert a load makes
    has to be one its grants and policies allow."""
    with pg_conn.cursor() as cur:
        did = loaded_dataset(cur)
        cur.execute("SET LOCAL ROLE bloom_writer")
    _, results, genes = load(de, pg_conn, did,
                             [entry(), entry(contrast="x_vs_y", tested=False)],
                             {KEY: rows()})
    de.check_not_loaded(pg_conn, did, "not-this-one")
    assert (results, genes) == (2, 2)
    pg_conn.rollback()
