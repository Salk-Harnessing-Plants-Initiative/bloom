"""
Integration tests for the database side of `scripts/ingest_scrnaseq_de.py`.

The rows this writes are the ones the migration's CHECK constraints were written
for, and a skipped comparison is the awkward shape: it names a contrast, so it
has to carry all five counts, and it has no file. Nothing but a real database
can tell whether the rows it builds are legal, so these write them for real.

Storage is a stand-in that records what it was asked to write.

LOCAL ONLY: every test rolls back.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "ingest_scrnaseq_de.py"


@pytest.fixture(scope="module")
def de():
    spec = importlib.util.spec_from_file_location("ingest_scrnaseq_de", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["ingest_scrnaseq_de"] = module
    spec.loader.exec_module(module)
    return module


class FakeStorage:
    def __init__(self):
        self.written: dict[str, bytes] = {}

    def upload(self, path: str, file: bytes, file_options: dict) -> None:
        self.written[path] = file


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


def catalogue(cur, dataset_id: int, cell_types: list[str]) -> None:
    cur.executemany(
        "INSERT INTO scrna_clusters (dataset_id, cluster_id, ordinal, name, color) "
        "VALUES (%s, %s, %s, %s, %s)",
        [(dataset_id, t, i, t, f"#00000{i}") for i, t in enumerate(cell_types)],
    )


def entry(celltype="Cortex", contrast="pFACT_vs_Col-0", tested=True,
          counts=None) -> dict:
    return {
        "celltype": celltype, "contrast": contrast,
        "group1": "pFACT", "group2": "Col-0",
        "n_group1": 10, "n_group2": 20, "tested": tested,
        "counts": counts or ({"n_genes_tested": 3, "n_significant_fdr": 2,
                              "n_significant_fdr_lfc": 2, "n_up": 1,
                              "n_down": 1} if tested else None),
    }


def rows(genes=("AT1G00001", "AT1G00002")) -> list[dict]:
    return [
        {"gene": g, "p_val": 0.001, "avg_log2FC": 1.0, "pct.1": 0.5,
         "pct.2": 0.25, "p_val_adj": 0.01, "_row": g,
         "_fdr": True, "_fdr_lfc": True}
        for g in genes
    ]


# --------------------------------------------------------------------------- #
# Finding the dataset and its cell types
# --------------------------------------------------------------------------- #


def test_an_unloaded_dataset_says_to_load_the_cells_first(de, pg_conn):
    with pg_conn.cursor() as cur:
        sid = species(cur)
    with pytest.raises(de.IngestError, match="Load its cells first"):
        de.open_dataset(pg_conn, "absent", sid)
    pg_conn.rollback()


def test_a_cell_type_the_catalogue_does_not_have_is_refused(de, pg_conn):
    """The panel lists cell types straight from these rows, so one the map has
    never heard of is offered and then colours nothing."""
    with pg_conn.cursor() as cur:
        sid = species(cur)
        did = dataset(cur, sid)
        catalogue(cur, did, ["Cortex", "Xylem"])
        with pytest.raises(de.IngestError, match="not in the catalogue"):
            de.check_cell_types(pg_conn, did, [entry(celltype="Phellem")])
    pg_conn.rollback()


def test_an_empty_catalogue_says_to_load_the_cells_first(de, pg_conn):
    with pg_conn.cursor() as cur:
        sid = species(cur)
        did = dataset(cur, sid)
        with pytest.raises(de.IngestError, match="Load its cells first"):
            de.check_cell_types(pg_conn, did, [entry()])
    pg_conn.rollback()


def test_cell_types_that_all_exist_are_accepted(de, pg_conn):
    with pg_conn.cursor() as cur:
        sid = species(cur)
        did = dataset(cur, sid)
        catalogue(cur, did, ["Cortex", "Xylem"])
        de.check_cell_types(pg_conn, did, [entry(), entry(celltype="Xylem")])
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Cross-checking the gene names against the ones the explorer knows
# --------------------------------------------------------------------------- #


def test_a_gene_the_dataset_does_not_have_is_refused(de, pg_conn):
    """Stripping the annotation release is what makes these names match the
    expression matrix, so a name that still does not match means the stripping
    was wrong for this export."""
    with pg_conn.cursor() as cur:
        sid = species(cur)
        did = dataset(cur, sid)
        cur.executemany(
            "INSERT INTO scrna_genes (dataset_id, gene_number, gene_name) "
            "VALUES (%s, %s, %s)",
            [(did, 0, "AT1G00001")],
        )
        with pytest.raises(de.IngestError, match="not registered for this dataset"):
            de.check_genes(pg_conn, did, {("Cortex", "c"): rows()})
    pg_conn.rollback()


def test_genes_that_all_exist_are_counted_as_checked(de, pg_conn):
    with pg_conn.cursor() as cur:
        sid = species(cur)
        did = dataset(cur, sid)
        cur.executemany(
            "INSERT INTO scrna_genes (dataset_id, gene_number, gene_name) "
            "VALUES (%s, %s, %s)",
            [(did, 0, "AT1G00001"), (did, 1, "AT1G00002"), (did, 2, "AT1G00003")],
        )
        assert de.check_genes(pg_conn, did, {("Cortex", "c"): rows()}) == (2, 3)
    pg_conn.rollback()


def test_no_registered_genes_means_no_cross_check_rather_than_a_refusal(de,
                                                                       pg_conn):
    """The gene counts are a separate step and may not have run. That is not an
    error here, but the summary says so rather than implying a check happened."""
    with pg_conn.cursor() as cur:
        sid = species(cur)
        did = dataset(cur, sid)
        assert de.check_genes(pg_conn, did, {("Cortex", "c"): rows()}) == (0, 0)
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Writing the rows the constraints were written for
# --------------------------------------------------------------------------- #


def test_a_tested_comparison_is_written_whole(de, pg_conn):
    with pg_conn.cursor() as cur:
        sid = species(cur)
        did = dataset(cur, sid)
        catalogue(cur, did, ["Cortex"])
    summary = [entry()]
    paths = {("Cortex", "pFACT_vs_Col-0"): "de/d/Cortex__pFACT_vs_Col-0.json"}
    store = FakeStorage()

    de.write_de(pg_conn, store, did, summary,
                {("Cortex", "pFACT_vs_Col-0"): rows()}, paths)

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT cluster_id, contrast, group1, group2, n_group1, n_group2, "
            "file_path, n_genes_tested, n_significant_fdr, "
            "n_significant_fdr_lfc, n_up, n_down "
            "FROM scrna_de WHERE dataset_id = %s", (did,),
        )
        assert cur.fetchall() == [(
            "Cortex", "pFACT_vs_Col-0", "pFACT", "Col-0", 10, 20,
            "de/d/Cortex__pFACT_vs_Col-0.json", 3, 2, 2, 1, 1,
        )]
    pg_conn.rollback()


def test_a_skipped_comparison_stores_five_zeros_and_no_file(de, pg_conn):
    """This is the shape the constraints are strict about: it names a contrast,
    so it has to carry all five counts, and blanks would switch off the
    arithmetic rules that compare them."""
    with pg_conn.cursor() as cur:
        sid = species(cur)
        did = dataset(cur, sid)
        catalogue(cur, did, ["Cortex"])

    de.write_de(pg_conn, FakeStorage(), did, [entry(tested=False)], {}, {})

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT file_path, n_group1, n_group2, n_genes_tested, "
            "n_significant_fdr, n_significant_fdr_lfc, n_up, n_down "
            "FROM scrna_de WHERE dataset_id = %s", (did,),
        )
        assert cur.fetchall() == [(None, 10, 20, 0, 0, 0, 0, 0)]
    pg_conn.rollback()


def test_the_written_file_holds_only_what_the_panel_reads(de, pg_conn):
    with pg_conn.cursor() as cur:
        sid = species(cur)
        did = dataset(cur, sid)
        catalogue(cur, did, ["Cortex"])
    key = ("Cortex", "pFACT_vs_Col-0")
    store = FakeStorage()

    de.write_de(pg_conn, store, did, [entry()], {key: rows()},
                {key: "de/d/Cortex.json"})

    written = json.loads(store.written["de/d/Cortex.json"])
    assert len(written) == 2
    assert list(written[0]) == ["gene", "p_val", "avg_log2FC", "pct.1",
                                "pct.2", "p_val_adj", "_row"]
    pg_conn.rollback()


def test_a_reload_replaces_rather_than_accumulates(de, pg_conn):
    with pg_conn.cursor() as cur:
        sid = species(cur)
        did = dataset(cur, sid)
        catalogue(cur, did, ["Cortex", "Xylem"])
    key = ("Cortex", "pFACT_vs_Col-0")

    de.write_de(pg_conn, FakeStorage(), did, [entry(), entry(celltype="Xylem")],
                {key: rows(), ("Xylem", "pFACT_vs_Col-0"): rows()},
                {key: "a.json", ("Xylem", "pFACT_vs_Col-0"): "b.json"})
    de.write_de(pg_conn, FakeStorage(), did, [entry()], {key: rows()},
                {key: "a.json"})

    with pg_conn.cursor() as cur:
        cur.execute("SELECT cluster_id FROM scrna_de WHERE dataset_id = %s", (did,))
        assert cur.fetchall() == [("Cortex",)]
    pg_conn.rollback()


def test_one_dataset_s_results_do_not_touch_another_s(de, pg_conn):
    with pg_conn.cursor() as cur:
        sid = species(cur)
        keep = dataset(cur, sid, "keep")
        catalogue(cur, keep, ["Cortex"])
        other = dataset(cur, sid, "other")
        catalogue(cur, other, ["Cortex"])
    key = ("Cortex", "pFACT_vs_Col-0")

    de.write_de(pg_conn, FakeStorage(), keep, [entry()], {key: rows()},
                {key: "keep.json"})
    de.write_de(pg_conn, FakeStorage(), other, [entry()], {key: rows()},
                {key: "other.json"})

    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM scrna_de WHERE dataset_id = %s", (keep,))
        assert cur.fetchone()[0] == 1
    pg_conn.rollback()


def test_every_comparison_of_the_real_shape_is_legal(de, pg_conn):
    """69 comparisons over 23 cell types and 3 contrasts, 46 tested — the shape
    of the real export, written against the real constraints."""
    cell_types = [f"Type{i}" for i in range(23)]
    contrasts = ["pFACT_vs_Col-0", "pFACT_vs_pHORST", "pHORST_vs_Col-0"]
    with pg_conn.cursor() as cur:
        sid = species(cur)
        did = dataset(cur, sid)
        catalogue(cur, did, cell_types)

    summary, groups, paths = [], {}, {}
    for i, t in enumerate(cell_types):
        for j, c in enumerate(contrasts):
            tested = (i * 3 + j) < 46
            summary.append(entry(celltype=t, contrast=c, tested=tested))
            if tested:
                key = (t, c)
                groups[key] = rows()
                summary[-1]["counts"] = de.recount(groups[key])
                paths[key] = f"de/d/{t}__{c}.json"

    objects, written = de.write_de(pg_conn, FakeStorage(), did, summary,
                                   groups, paths)
    assert (objects, written) == (46, 69)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FILTER (WHERE file_path IS NOT NULL), "
            "count(*) FILTER (WHERE file_path IS NULL) "
            "FROM scrna_de WHERE dataset_id = %s", (did,),
        )
        assert cur.fetchone() == (46, 23)
    pg_conn.rollback()
