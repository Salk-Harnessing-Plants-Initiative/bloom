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
    }


def species(cur) -> int:
    tag = uuid.uuid4().hex[:10]
    cur.execute(
        "INSERT INTO species (common_name, genus, species) VALUES (%s, %s, %s) "
        "RETURNING id",
        (f"ingest-{tag}", f"Ingestus-{tag}", f"testis-{tag}"),
    )
    return cur.fetchone()[0]


def run(ingest, conn, name, species_id, table, checksum="sha", annotation="ann",
        create=True):
    """Load, returning just the id and the stored count.

    `create` defaults to True here because almost every test starts from a
    dataset that does not exist yet; the tests that care about the flag pass it
    explicitly.
    """
    dataset_id, stored, _created = ingest.load(
        conn, name, species_id, table, checksum,
        "log1p normalised counts", annotation, create=create,
    )
    return dataset_id, stored


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
            "SELECT cluster_id, ordinal, name, color FROM scrna_clusters "
            "WHERE dataset_id = %s ORDER BY ordinal",
            (dataset_id,),
        )
        catalogue = cur.fetchall()
        # Ordinals contiguous from zero, in sorted label order, name mirroring
        # id, and a distinct colour each -- a repeat draws two cell types
        # identically in both the plot and the legend.
        assert catalogue == [
            ("Cortex", 0, "Cortex", ingest.PALETTE[0]),
            ("Phellem", 1, "Phellem", ingest.PALETTE[1]),
            ("Xylem", 2, "Xylem", ingest.PALETTE[2]),
        ]

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
            "(dataset_id, cluster_id, neighbor_cluster_id, rank, similarity) "
            "VALUES (%s, 'A', 'B', 1, 0.5)", (dataset_id,),
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


def test_the_cell_type_column_is_recorded_without_touching_the_genome_annotation(
    ingest, pg_conn
):
    """Which obs column the cell types came from has to be recorded, because the
    differential expression results name cell types and must match. It goes in
    `metadata`: `annotation` means the genome annotation, is written by the
    uploader and is on screen beside `assembly`."""
    with pg_conn.cursor() as cur:
        sid = species(cur)
        cur.execute(
            "INSERT INTO scrna_datasets (name, species_id, annotation, metadata) "
            "VALUES ('ann', %s, 'Araport11', '{\"strain\": \"Col-0\"}'::jsonb) "
            "RETURNING id", (sid,),
        )
        existing = cur.fetchone()[0]
        dataset_id, _ = run(ingest, pg_conn, "ann", sid, cells(["A", "B"]),
                            annotation="nn_label_plain")
        assert dataset_id == existing
        cur.execute(
            "SELECT annotation, metadata FROM scrna_datasets WHERE id = %s",
            (dataset_id,),
        )
        annotation, metadata = cur.fetchone()
        assert annotation == "Araport11", "the genome annotation must survive"
        assert metadata["cell_type_column"] == "nn_label_plain"
        assert metadata["strain"] == "Col-0", "other metadata must survive"
    pg_conn.rollback()


def test_differential_expression_blocks_a_reload(ingest, pg_conn):
    """DE rows name cell types and have no foreign key to the catalogue, so a
    reload on a different annotation leaves them naming types that do not
    exist -- nothing errors and nothing cascades."""
    with pg_conn.cursor() as cur:
        sid = species(cur)
        dataset_id, _ = run(ingest, pg_conn, "de", sid, cells(["A", "B"]))
        cur.execute(
            "INSERT INTO scrna_de (dataset_id, cluster_id, file_path) "
            "VALUES (%s, 'A', 'de/A.tsv')", (dataset_id,),
        )
        refuses(ingest, pg_conn, "differential expression", "de", sid,
                cells(["A", "B"]))
        cur.execute("SELECT count(*) FROM scrna_de WHERE dataset_id = %s",
                    (dataset_id,))
        assert cur.fetchone()[0] == 1
    pg_conn.rollback()


def test_a_reload_keeps_hand_edited_names_and_colours(ingest, pg_conn):
    """Cluster names and colours are edited after a load -- the backfill seeds
    them and says to fix the biology in Studio -- and cannot be rebuilt from the
    file. A surviving cell type keeps both; a new one takes a colour no
    surviving type is using."""
    with pg_conn.cursor() as cur:
        sid = species(cur)
        dataset_id, _ = run(ingest, pg_conn, "curated", sid, cells(["A", "C"]))
        cur.execute(
            "UPDATE scrna_clusters SET name = 'Phellem (periderm layer 2)', "
            "color = '#112233' WHERE dataset_id = %s AND cluster_id = 'C'",
            (dataset_id,),
        )
        # 'B' is new and sorts before 'C', so without carry-forward C's colour
        # would shift to whatever its new ordinal points at.
        run(ingest, pg_conn, "curated", sid, cells(["A", "B", "C"]))

        cur.execute(
            "SELECT cluster_id, name, color FROM scrna_clusters "
            "WHERE dataset_id = %s ORDER BY cluster_id", (dataset_id,),
        )
        rows = cur.fetchall()
        assert rows[2] == ("C", "Phellem (periderm layer 2)", "#112233")
        assert rows[0] == ("A", "A", ingest.PALETTE[0]), "unedited types keep theirs"
        colours = [c for _, _, c in rows]
        assert len(set(colours)) == 3, f"a colour was reused: {colours}"
    pg_conn.rollback()


def test_a_refusal_commits_nothing(ingest, pg_conn):
    """`load()` must never commit: the caller's context manager is what makes
    the whole load one transaction. Nothing asserted this, so a stray commit
    inside `load()` would make a refusal destructive and stay invisible."""
    with pg_conn.cursor() as cur:
        sid = species(cur)
        dataset_id, _ = run(ingest, pg_conn, "nocommit", sid, cells(["A", "B"]))
        cur.execute(
            "INSERT INTO scrna_genes (dataset_id, gene_number, gene_name) "
            "VALUES (%s, 0, 'AT1G01010') RETURNING id", (dataset_id,),
        )
        cur.execute(
            "INSERT INTO scrna_counts (dataset_id, gene_id, counts_object_path) "
            "VALUES (%s, %s, 'scrna/counts/x/AT1G01010.json')",
            (dataset_id, cur.fetchone()[0]),
        )
        refuses(ingest, pg_conn, "per-gene expression", "nocommit", sid,
                cells(["A", "B"]))

    pg_conn.rollback()
    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM scrna_datasets WHERE id = %s",
                    (dataset_id,))
        assert cur.fetchone()[0] == 0, "the load committed something"
        cur.execute("SELECT count(*) FROM scrna_cells WHERE dataset_id = %s",
                    (dataset_id,))
        assert cur.fetchone()[0] == 0


def test_a_curated_colour_in_lower_case_still_reserves_its_slot(ingest, pg_conn):
    """Hex case is not meaning. A surviving type holding '#e15759' must stop a
    new type being handed '#E15759'. This repo's own colour backfill writes
    lower case and 16 of the 23 palette entries differ from it only in case, so
    comparing raw strings hands out a duplicate -- the exact defect a 24-type
    load is refused to avoid.

    Two new cell types, so the spare list has to reach the curated colour."""
    with pg_conn.cursor() as cur:
        sid = species(cur)
        dataset_id, _ = run(ingest, pg_conn, "case", sid, cells(["A", "C"]))
        cur.execute(
            "UPDATE scrna_clusters SET color = %s "
            "WHERE dataset_id = %s AND cluster_id = 'C'",
            (ingest.PALETTE[2].lower(), dataset_id),
        )
        run(ingest, pg_conn, "case", sid, cells(["A", "B", "C", "D"]))

        cur.execute("SELECT cluster_id, color FROM scrna_clusters "
                    "WHERE dataset_id = %s ORDER BY cluster_id", (dataset_id,))
        rows = cur.fetchall()
        assert dict(rows)["C"] == ingest.PALETTE[2].lower(), "curation must survive"
        colours = [c.lower() for _, c in rows]
        assert len(set(colours)) == 4, f"two cell types share a colour: {rows}"
    pg_conn.rollback()


def test_a_vanished_cell_type_does_not_hold_on_to_its_colour(ingest, pg_conn):
    """Only surviving types keep theirs. Counting departed ones would shrink the
    spare list until a full-width catalogue could not be coloured at all."""
    n = len(ingest.PALETTE)
    with pg_conn.cursor() as cur:
        sid = species(cur)
        first = [f"old{i}" for i in range(n)]
        dataset_id, _ = run(ingest, pg_conn, "swap", sid, cells(first))
        run(ingest, pg_conn, "swap", sid, cells([f"new{i}" for i in range(n)]))
        cur.execute("SELECT color FROM scrna_clusters WHERE dataset_id = %s",
                    (dataset_id,))
        colours = [c for (c,) in cur.fetchall()]
        assert len(colours) == n
        assert len(set(colours)) == n, "a colour was reused"
    pg_conn.rollback()


def test_a_blank_curated_name_or_colour_falls_back(ingest, pg_conn):
    """Spaces are not a name and not a colour. The file's own labels are refused
    when blank, so blank values arriving from the database get the same
    treatment rather than becoming an empty legend entry or an unpaintable
    swatch."""
    with pg_conn.cursor() as cur:
        sid = species(cur)
        dataset_id, _ = run(ingest, pg_conn, "blank", sid, cells(["A", "B"]))
        cur.execute(
            "UPDATE scrna_clusters SET name = '   ', color = '  ' "
            "WHERE dataset_id = %s AND cluster_id = 'A'", (dataset_id,),
        )
        run(ingest, pg_conn, "blank", sid, cells(["A", "B"]))
        cur.execute("SELECT name, color FROM scrna_clusters WHERE dataset_id = %s "
                    "AND cluster_id = 'A'", (dataset_id,))
        name, color = cur.fetchone()
        assert name == "A"
        assert color in ingest.PALETTE, color
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Registering a dataset is deliberate, so a typo cannot fork one
# --------------------------------------------------------------------------- #
#
# Creating on a lookup miss is how a mistyped name forks a dataset: the load
# succeeds, reports what a replace reports, and the next run with the name
# spelled right finds two and refuses from then on. Count files are keyed by
# dataset name, so the copies would share a storage namespace as well.


def test_an_unknown_dataset_name_is_refused_without_create(ingest, pg_conn):
    with pg_conn.cursor() as cur:
        sid = species(cur)
        refuses(ingest, pg_conn, "Pass --create", "brand new", sid,
                cells(["A", "B"]), create=False)

        cur.execute(
            "SELECT count(*) FROM scrna_datasets WHERE name = %s AND species_id = %s",
            ("brand new", sid),
        )
        assert cur.fetchone()[0] == 0, "the refusal must not have registered it"
    pg_conn.rollback()


def test_create_registers_the_dataset_and_says_so(ingest, pg_conn):
    with pg_conn.cursor() as cur:
        sid = species(cur)
        dataset_id, stored, created = ingest.load(
            pg_conn, "fresh", sid, cells(["A", "B"]), "sha",
            "log1p normalised counts", "ann", create=True,
        )
        assert created is True
        assert stored == 2
    pg_conn.rollback()


def test_a_reload_reports_that_it_replaced_rather_than_created(ingest, pg_conn):
    """The two cases print the same sentence otherwise, and which one happened
    is exactly what an operator needs to see after a mistyped name."""
    with pg_conn.cursor() as cur:
        sid = species(cur)
        first, _ = run(ingest, pg_conn, "twice", sid, cells(["A", "B"]))
        again, _, created = ingest.load(
            pg_conn, "twice", sid, cells(["A", "B"]), "sha-2",
            "log1p normalised counts", "ann", create=False,
        )
        assert created is False
        assert again == first, "a reload must stay on the same dataset"
    pg_conn.rollback()


def test_a_reload_needs_no_create_flag(ingest, pg_conn):
    """--create guards registration only. Requiring it on every run would train
    an operator to pass it always, which is the same as not having it."""
    with pg_conn.cursor() as cur:
        sid = species(cur)
        first, _ = run(ingest, pg_conn, "existing", sid, cells(["A", "B"]))
        again, _ = run(ingest, pg_conn, "existing", sid, cells(["A", "C"]),
                       "sha-2", create=False)
        assert again == first
    pg_conn.rollback()


def test_a_padded_name_finds_the_dataset_it_meant(ingest, pg_conn):
    """A trailing space from a paste or a shell is the commonest route into a
    forked dataset, so the name is trimmed before the lookup."""
    with pg_conn.cursor() as cur:
        sid = species(cur)
        first, _ = run(ingest, pg_conn, "padded", sid, cells(["A", "B"]))
        again, _ = run(ingest, pg_conn, "  padded  ", sid, cells(["A", "B"]),
                       "sha-2", create=False)
        assert again == first, "whitespace must not register a second copy"

        cur.execute(
            "SELECT count(*) FROM scrna_datasets WHERE species_id = %s", (sid,)
        )
        assert cur.fetchone()[0] == 1
    pg_conn.rollback()


def test_a_blank_dataset_name_is_refused(ingest, pg_conn):
    with pg_conn.cursor() as cur:
        sid = species(cur)
        refuses(ingest, pg_conn, "name is blank", "   ", sid, cells(["A", "B"]))
    pg_conn.rollback()
