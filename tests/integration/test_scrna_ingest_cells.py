"""
Integration tests for `load()` in `scripts/ingest_scrnaseq.py`.

These drive the real write path against a real database. `load()` is one
transaction over four statements whose order is forced by the schema — cells
reference the cluster catalogue with ON DELETE RESTRICT, and two other tables
cascade off it — so the parts pinned here are the ones whose failure mode is a
dataset that loads without complaint and is wrong.

The most important is the order the cells come back in. A later change serves
per-gene expression as a bare array indexed positionally against
`scrna_cell_arrays`, which orders by `cell_number`. If that order or its base
ever changes, every gene's expression paints onto the wrong cell, and nothing
downstream can tell.

No `.h5ad` is needed: the tests hand `load()` the dict that `read_cells` returns.

LOCAL ONLY: every test rolls back, leaving the database untouched.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "ingest_scrnaseq.py"


@pytest.fixture(scope="module")
def ingest():
    spec = importlib.util.spec_from_file_location("ingest_scrnaseq", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["ingest_scrnaseq"] = module
    spec.loader.exec_module(module)
    return module


def cells(labels: list[str], samples: list[str] | None = None) -> dict:
    """A cell table shaped like `read_cells` returns, with distinguishable
    coordinates so a swap or a reversal is visible."""
    n = len(labels)
    return {
        "n_cells": n,
        "n_genes": 100,
        "x": [float(i) for i in range(n)],
        "y": [float(i) + 0.5 for i in range(n)],
        "labels": labels,
        "samples": samples or ["Col-0"] * n,
        "levels": sorted(set(labels)),
        "barcodes": [f"BC{i}" for i in range(n)],
        "purity": 0.9,
    }


def species(cur) -> int:
    tag = uuid.uuid4().hex[:10]
    cur.execute(
        "INSERT INTO species (common_name, genus, species) VALUES (%s, %s, %s) "
        "RETURNING id",
        (f"ingest-{tag}", f"Ingestus-{tag}", f"testis-{tag}"),
    )
    return cur.fetchone()[0]


def run(ingest, conn, name, species_id, table, checksum="sha", annotation="ann"):
    return ingest.load(conn, name, species_id, table, checksum,
                       "log1p normalised counts", annotation)


def refuses(ingest, conn, match, *args, **kwargs):
    """Assert the load is refused, without losing the rest of the test.

    A refusal aborts the transaction it happened in, so the call goes inside a
    savepoint -- otherwise checking what survived would find the test's own
    setup rolled back too.
    """
    with pytest.raises(ingest.IngestError, match=match):
        with conn.transaction():
            run(ingest, conn, *args, **kwargs)


# --------------------------------------------------------------------------- #
# A first load
# --------------------------------------------------------------------------- #


def test_a_first_load_writes_the_three_tables(ingest, pg_conn):
    labels = ["Xylem", "Cortex", "Phellem", "Cortex"]
    with pg_conn.cursor() as cur:
        sid = species(cur)
        dataset_id, stored = run(ingest, pg_conn, "first", sid, cells(labels))
        assert stored == 4

        cur.execute(
            "SELECT cluster_id, ordinal, name FROM scrna_clusters "
            "WHERE dataset_id = %s ORDER BY ordinal",
            (dataset_id,),
        )
        catalogue = cur.fetchall()
        # Ordinals contiguous from zero, in sorted label order, name mirroring id.
        assert catalogue == [("Cortex", 0, "Cortex"), ("Phellem", 1, "Phellem"),
                             ("Xylem", 2, "Xylem")]

        cur.execute(
            "SELECT cell_number, barcode, x, y, cluster_id, replicate "
            "FROM scrna_cells WHERE dataset_id = %s ORDER BY cell_number",
            (dataset_id,),
        )
        assert cur.fetchall() == [
            (0, "BC0", 0.0, 0.5, "Xylem", "Col-0"),
            (1, "BC1", 1.0, 1.5, "Cortex", "Col-0"),
            (2, "BC2", 2.0, 2.5, "Phellem", "Col-0"),
            (3, "BC3", 3.0, 3.5, "Cortex", "Col-0"),
        ]
    pg_conn.rollback()


def test_the_view_returns_the_cells_in_file_order(ingest, pg_conn):
    """The one that protects per-gene expression. A later change indexes an
    expression array positionally against this order, so a reversed or 1-based
    `cell_number` would paint every gene onto the wrong cell."""
    labels = ["Xylem", "Cortex", "Phellem", "Cortex"]
    with pg_conn.cursor() as cur:
        sid = species(cur)
        dataset_id, _ = run(ingest, pg_conn, "order", sid, cells(labels))
        cur.execute("SELECT x, y, cluster_ordinal FROM scrna_cell_arrays(%s)",
                    (dataset_id,))
        # Xylem=2, Cortex=0, Phellem=1 by sorted order.
        assert cur.fetchall() == [
            (0.0, 0.5, 2), (1.0, 1.5, 0), (2.0, 2.5, 1), (3.0, 3.5, 0)
        ]
    pg_conn.rollback()


def test_no_cell_is_left_without_a_cluster(ingest, pg_conn):
    with pg_conn.cursor() as cur:
        sid = species(cur)
        dataset_id, _ = run(ingest, pg_conn, "orphans", sid,
                            cells(["A", "B", "A", "C"]))
        cur.execute(
            "SELECT count(*) FROM scrna_cell_arrays(%s) WHERE cluster_ordinal = 255",
            (dataset_id,),
        )
        assert cur.fetchone()[0] == 0
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Loading again
# --------------------------------------------------------------------------- #


def test_a_second_load_replaces_rather_than_accumulates(ingest, pg_conn):
    """Cells reference the catalogue with ON DELETE RESTRICT, so the deletes have
    to happen in the right order or a re-run cannot start."""
    with pg_conn.cursor() as cur:
        sid = species(cur)
        first, _ = run(ingest, pg_conn, "again", sid, cells(["A", "B"]), "sha-1")

        table = cells(["A", "B"])
        table["x"] = [90.0, 91.0]
        second, stored = run(ingest, pg_conn, "again", sid, table, "sha-2")

        assert second == first and stored == 2
        cur.execute("SELECT count(*) FROM scrna_cells WHERE dataset_id = %s", (first,))
        assert cur.fetchone()[0] == 2
        cur.execute("SELECT x FROM scrna_cells WHERE dataset_id = %s "
                    "ORDER BY cell_number", (first,))
        assert [r[0] for r in cur.fetchall()] == [90.0, 91.0]
        cur.execute("SELECT source_checksum FROM scrna_datasets WHERE id = %s", (first,))
        assert cur.fetchone()[0] == "sha-2"
    pg_conn.rollback()


def test_a_changed_cell_type_set_leaves_nothing_behind(ingest, pg_conn):
    with pg_conn.cursor() as cur:
        sid = species(cur)
        dataset_id, _ = run(ingest, pg_conn, "changed", sid, cells(["A", "B", "C"]))
        run(ingest, pg_conn, "changed", sid, cells(["A", "D", "E"]))

        cur.execute(
            "SELECT cluster_id FROM scrna_clusters WHERE dataset_id = %s "
            "ORDER BY ordinal", (dataset_id,),
        )
        assert [r[0] for r in cur.fetchall()] == ["A", "D", "E"]
        cur.execute(
            "SELECT count(*) FROM scrna_cell_arrays(%s) WHERE cluster_ordinal = 255",
            (dataset_id,),
        )
        assert cur.fetchone()[0] == 0
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Refusals, and that they leave nothing behind
# --------------------------------------------------------------------------- #


def test_a_count_mismatch_refuses_and_keeps_the_previous_load(ingest, pg_conn):
    with pg_conn.cursor() as cur:
        sid = species(cur)
        dataset_id, _ = run(ingest, pg_conn, "count", sid, cells(["A", "B"]), "sha-1")

        broken = cells(["A", "B"])
        broken["n_cells"] = 3          # what the file claimed
        refuses(ingest, pg_conn, "wrote 2 cells", "count", sid, broken, "sha-2")

        cur.execute("SELECT source_checksum, n_cells FROM scrna_datasets "
                    "WHERE id = %s", (dataset_id,))
        assert cur.fetchone() == ("sha-1", 2)
    pg_conn.rollback()


def test_ragged_columns_refuse(ingest, pg_conn):
    """zip() would silently truncate; the count check is what notices."""
    with pg_conn.cursor() as cur:
        sid = species(cur)
        table = cells(["A", "B", "C"])
        table["labels"] = ["A", "B"]
        table["levels"] = ["A", "B"]
        refuses(ingest, pg_conn, "wrote 2 cells", "ragged", sid, table)
    pg_conn.rollback()


def test_per_cluster_statistics_block_a_reload(ingest, pg_conn):
    """They cascade off the catalogue, and nothing here rebuilds them."""
    with pg_conn.cursor() as cur:
        sid = species(cur)
        dataset_id, _ = run(ingest, pg_conn, "stats", sid, cells(["A", "B"]))
        cur.execute(
            "INSERT INTO scrna_cluster_stats (dataset_id, cluster_id, cell_count, pct) "
            "VALUES (%s, 'A', 1, 50.0)", (dataset_id,),
        )
        refuses(ingest, pg_conn, "per-cluster statistic", "stats", sid,
                cells(["A", "B"]))
        cur.execute("SELECT count(*) FROM scrna_cluster_stats WHERE dataset_id = %s",
                    (dataset_id,))
        assert cur.fetchone()[0] == 1, "the statistics must survive the refusal"
    pg_conn.rollback()


def test_the_neighbour_graph_blocks_a_reload(ingest, pg_conn):
    """It cascades off the catalogue too. Guarding only the statistics let a
    reload delete this silently."""
    with pg_conn.cursor() as cur:
        sid = species(cur)
        dataset_id, _ = run(ingest, pg_conn, "neigh", sid, cells(["A", "B"]))
        cur.execute(
            "INSERT INTO scrna_cluster_neighbors "
            "(dataset_id, cluster_id, neighbor_cluster_id, rank) "
            "VALUES (%s, 'A', 'B', 1)", (dataset_id,),
        )
        refuses(ingest, pg_conn, "neighbour row", "neigh", sid, cells(["A", "B"]))
        cur.execute(
            "SELECT count(*) FROM scrna_cluster_neighbors WHERE dataset_id = %s",
            (dataset_id,),
        )
        assert cur.fetchone()[0] == 1, "the neighbour graph must survive"
    pg_conn.rollback()


def test_existing_gene_expression_blocks_a_reload(ingest, pg_conn):
    """Expression is served as an array indexed by cell_number, so renumbering
    the cells would paint every gene onto different ones."""
    with pg_conn.cursor() as cur:
        sid = species(cur)
        dataset_id, _ = run(ingest, pg_conn, "counts", sid, cells(["A", "B"]))
        cur.execute(
            "INSERT INTO scrna_genes (dataset_id, gene_number, gene_name) "
            "VALUES (%s, 0, 'AT1G01010') RETURNING id", (dataset_id,),
        )
        cur.execute(
            "INSERT INTO scrna_counts (dataset_id, gene_id, counts_object_path) "
            "VALUES (%s, %s, 'scrna/counts/x/AT1G01010.json')",
            (dataset_id, cur.fetchone()[0]),
        )
        refuses(ingest, pg_conn, "per-gene expression", "counts", sid,
                cells(["A", "B"]))
        cur.execute("SELECT count(*) FROM scrna_counts WHERE dataset_id = %s",
                    (dataset_id,))
        assert cur.fetchone()[0] == 1
    pg_conn.rollback()


def test_two_datasets_with_one_name_refuse(ingest, pg_conn):
    with pg_conn.cursor() as cur:
        sid = species(cur)
        cur.execute("INSERT INTO scrna_datasets (name, species_id) VALUES ('dup', %s)",
                    (sid,))
        cur.execute("INSERT INTO scrna_datasets (name, species_id) VALUES ('dup', %s)",
                    (sid,))
        refuses(ingest, pg_conn, "cannot tell which", "dup", sid, cells(["A", "B"]))
    pg_conn.rollback()


def test_a_soft_deleted_dataset_is_not_resurrected(ingest, pg_conn):
    """Its rows would be invisible to every reader, so a load into it would
    appear to succeed and show nothing."""
    with pg_conn.cursor() as cur:
        sid = species(cur)
        cur.execute(
            "INSERT INTO scrna_datasets (name, species_id, deleted_at) "
            "VALUES ('gone', %s, now()) RETURNING id", (sid,),
        )
        buried = cur.fetchone()[0]
        fresh, _ = run(ingest, pg_conn, "gone", sid, cells(["A", "B"]))
        assert fresh != buried
        cur.execute("SELECT n_cells FROM scrna_datasets WHERE id = %s", (buried,))
        assert cur.fetchone()[0] is None
    pg_conn.rollback()


def test_the_annotation_column_is_recorded(ingest, pg_conn):
    """The same file loaded on a different annotation gives a different
    catalogue, so the checksum alone cannot tell two loads apart."""
    with pg_conn.cursor() as cur:
        sid = species(cur)
        dataset_id, _ = run(ingest, pg_conn, "ann", sid, cells(["A", "B"]),
                            annotation="nn_label_plain")
        cur.execute("SELECT annotation FROM scrna_datasets WHERE id = %s", (dataset_id,))
        assert cur.fetchone()[0] == "nn_label_plain"
    pg_conn.rollback()
